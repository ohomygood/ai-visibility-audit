"""Site checks: AI-crawler access, robots.txt, structured data, llms.txt."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http import PoliteSession, BROWSER_UA

# Official user-agent strings as published by each vendor.
AI_BOTS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "ChatGPT-User": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}
# robots.txt tokens we evaluate (superset of the UA probes).
ROBOTS_TOKENS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
                 "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "CCBot", "Bytespider"]
# Which tokens matter for being *recommended* (search/answer-time) vs training only.
ANSWER_TIME = {"OAI-SearchBot", "ChatGPT-User", "Claude-SearchBot", "Claude-User", "PerplexityBot", "Perplexity-User"}

BLOCK_STATUSES = {401, 403, 406, 429, 503}
CHALLENGE_MARKERS = ("cf-chl", "challenge-platform", "Just a moment...", "Attention Required! | Cloudflare",
                     "captcha-delivery.com", "px-captcha", "_Incapsula_Resource", "Access Denied")


def cdn_of(headers: dict) -> str:
    h = {k.lower(): v.lower() for k, v in headers.items()}
    if "cf-ray" in h or h.get("server", "") == "cloudflare":
        return "cloudflare"
    if "x-akamai-transformed" in h or "akamai" in h.get("server", ""):
        return "akamai"
    if "x-sucuri-id" in h:
        return "sucuri"
    if "x-iinfo" in h or "incap" in h.get("set-cookie", ""):
        return "imperva"
    if "x-datadome" in h or "datadome" in h.get("set-cookie", ""):
        return "datadome"
    if "x-amz-cf-id" in h:
        return "cloudfront"
    if "x-served-by" in h and "cache-" in h.get("x-served-by", ""):
        return "fastly"
    return ""


def is_challenge(f) -> bool:
    if f.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = f.text[:6000] if f.text else ""
    return any(m in head for m in CHALLENGE_MARKERS) and len(f.text) < 60000


# ---------------------------------------------------------------- bot access
def check_bot_access(sess: PoliteSession, urls: list[str], baseline: dict) -> dict:
    """Request each URL as each AI crawler. ``baseline`` maps url -> Fetch done with a browser UA."""
    results = {}
    for bot, ua in AI_BOTS.items():
        rows = []
        for u in urls:
            f = sess.get(u, ua=ua, ua_label=bot)
            if f.status == 429:  # rate limited, not necessarily blocked: back off once and retry
                try:
                    wait = min(float(f.headers.get("retry-after", "4")), 8.0)
                except ValueError:
                    wait = 4.0
                time.sleep(wait)
                f = sess.get(u, ua=ua, ua_label=bot + " (retry)")
            b = baseline.get(u)
            challenge = f.status is not None and is_challenge(f)
            rate_limited = f.status == 429 and not challenge
            blocked = (f.status in BLOCK_STATUSES and not rate_limited) or challenge or f.status is None
            parity = None
            if b is not None and b.ok and f.ok:
                parity = ("application/ld+json" in f.text) == ("application/ld+json" in b.text)
            rows.append({"url": u, "status": f.status, "error": f.error, "challenge": challenge, "rate_limited": rate_limited,
                         "blocked": blocked, "cdn": cdn_of(f.headers), "content_parity": parity,
                         "baseline_status": b.status if b else None})
        n_block = sum(r["blocked"] for r in rows)
        n_rl = sum(r["rate_limited"] for r in rows)
        results[bot] = {"pages": rows, "blocked_pages": n_block, "rate_limited_pages": n_rl, "total_pages": len(rows),
                        "verdict": "blocked" if n_block == len(rows) else ("partial" if n_block else "ok")}
    baseline_blocked = all((b.status in BLOCK_STATUSES or b.status is None) for b in baseline.values()) if baseline else False
    cdns = {r["cdn"] for v in results.values() for r in v["pages"] if r["cdn"]}
    return {"bots": results, "baseline_blocked": baseline_blocked, "cdn": sorted(cdns)}


# ---------------------------------------------------------------- robots.txt
@dataclass
class RobotsGroup:
    agents: list = field(default_factory=list)
    rules: list = field(default_factory=list)  # (allow: bool, pattern)


def parse_robots(text: str) -> tuple[list[RobotsGroup], list[str]]:
    groups, sitemaps, cur, last_was_agent = [], [], None, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if cur is None or not last_was_agent:
                cur = RobotsGroup()
                groups.append(cur)
            cur.agents.append(v.lower())
            last_was_agent = True
        elif k in ("allow", "disallow"):
            last_was_agent = False
            if cur is None:
                continue
            if k == "disallow" and v == "":
                continue  # empty disallow = allow all
            cur.rules.append((k == "allow", v))
        elif k == "sitemap":
            sitemaps.append(v)
            last_was_agent = False
        else:
            last_was_agent = False
    return groups, sitemaps


def _pattern_match(pattern: str, path: str) -> bool:
    rx = "^" + re.escape(pattern).replace(r"\*", ".*")
    if rx.endswith(r"\$"):
        rx = rx[:-2] + "$"
    return re.match(rx, path) is not None


def robots_allows(groups: list[RobotsGroup], token: str, path: str) -> tuple[bool, str]:
    """RFC 9309: pick the most specific group for the token, else '*'; longest match wins, allow wins ties."""
    t = token.lower()
    chosen = [g for g in groups if t in g.agents]
    matched_by = token
    if not chosen:
        chosen = [g for g in groups if "*" in g.agents]
        matched_by = "*"
    rules = [r for g in chosen for r in g.rules]
    best = None
    for allow, pat in rules:
        if _pattern_match(pat, path):
            key = (len(pat), allow)
            if best is None or key > best[0]:
                best = (key, allow, pat)
    if best is None:
        return True, f"no matching rule (group: {matched_by})"
    return best[1], f"{'Allow' if best[1] else 'Disallow'}: {best[2]} (group: {matched_by})"


def check_robots(sess: PoliteSession, root: str, sample_paths: list[str]) -> dict:
    f = sess.get(urljoin(root, "robots.txt"), ua=BROWSER_UA, ua_label="discovery", accept="text/plain")
    if f.status == 404 or (f.ok and "<html" in f.text[:500].lower()):
        return {"exists": False, "status": f.status, "tokens": {t: {"allowed": True, "detail": "no robots.txt"}
                                                                  for t in ROBOTS_TOKENS}, "sitemaps": [], "raw": ""}
    if not f.ok:
        return {"exists": None, "status": f.status, "error": f.error, "tokens": {}, "sitemaps": [], "raw": ""}
    groups, sitemaps = parse_robots(f.text)
    tokens = {}
    for t in ROBOTS_TOKENS:
        per_path = {p: robots_allows(groups, t, p) for p in sample_paths}
        allowed = all(a for a, _ in per_path.values())
        tokens[t] = {"allowed": allowed, "answer_time": t in ANSWER_TIME,
                     "explicit_group": any(t.lower() in g.agents for g in groups),
                     "detail": {p: d for p, (a, d) in per_path.items()},
                     "blocked_paths": [p for p, (a, _) in per_path.items() if not a]}
    return {"exists": True, "status": f.status, "tokens": tokens, "sitemaps": sitemaps, "raw": f.text[:20000]}


# ---------------------------------------------------------------- structured data
SCHEMA_RULES = {
    # type: (required, recommended)  — based on Google rich-result docs + schema.org expectations
    "Product": (["name"], ["image", "description", "brand", "sku", "offers", "aggregateRating|review", "gtin|gtin8|gtin12|gtin13|gtin14|mpn"]),
    "Offer": (["price|priceSpecification", "priceCurrency|priceSpecification"], ["availability", "url", "priceValidUntil", "itemCondition", "shippingDetails", "hasMerchantReturnPolicy"]),
    "AggregateOffer": (["lowPrice", "priceCurrency"], ["highPrice", "offerCount"]),
    "AggregateRating": (["ratingValue", "ratingCount|reviewCount"], ["bestRating"]),
    "Organization": (["name"], ["url", "logo", "sameAs", "contactPoint|email|telephone"]),
}
ORG_TYPES = {"Organization", "OnlineStore", "Corporation", "LocalBusiness", "Store", "Brand", "OnlineBusiness"}


def _types(node) -> list[str]:
    t = node.get("@type", [])
    t = t if isinstance(t, list) else [t]
    return [str(x).rsplit("/", 1)[-1] for x in t]


def _flatten(obj, out: list):
    if isinstance(obj, list):
        for x in obj:
            _flatten(x, out)
    elif isinstance(obj, dict):
        if "@graph" in obj:
            _flatten(obj["@graph"], out)
        if "@type" in obj:
            out.append(obj)
        for k, v in obj.items():
            if k != "@graph" and isinstance(v, (dict, list)):
                _flatten(v, out)


REF_KEYS = {"@type", "@id", "@context", "url", "sameAs"}
SUB_KEYS = ("offers", "aggregateRating", "review", "hasVariant")


def _is_ref(node: dict) -> bool:
    """A bare reference like {"@type": "Product", "url": "..."} is not a standalone entity."""
    return not (set(node) - REF_KEYS - {"_source"})


def _entities(obj, out: list, parent_typed: bool = False):
    """Validatable entities: top-level typed nodes (incl. @graph members and children of untyped wrappers),
    plus their offers / aggregateRating / review / substantive hasVariant children."""
    if isinstance(obj, list):
        for x in obj:
            _entities(x, out, parent_typed)
        return
    if not isinstance(obj, dict):
        return
    if "@graph" in obj:
        _entities(obj["@graph"], out, False)
    typed = "@type" in obj
    if typed and not parent_typed and not _is_ref(obj):
        out.append(obj)
        for k in SUB_KEYS:
            v = obj.get(k)
            for c in (v if isinstance(v, list) else [v]):
                if isinstance(c, dict) and "@type" in c and not _is_ref(c):
                    if k == "hasVariant":
                        _entities(c, out, False)
                    else:
                        out.append(c)
    for k, v in obj.items():
        if k != "@graph" and isinstance(v, (dict, list)) and not (typed and k in SUB_KEYS):
            _entities(v, out, typed)


def _microdata(soup) -> list[dict]:
    def walk(el) -> dict:
        it = (el.get("itemtype") or "").split()
        node = {"@type": it[0].rsplit("/", 1)[-1] if it else "Thing", "_source": "microdata"}
        for c in el.find_all(attrs={"itemprop": True}):
            if c.find_parent(attrs={"itemscope": True}) is not el:
                continue
            val = walk(c) if c.has_attr("itemscope") else (
                c.get("content") or c.get("href") or c.get("src") or c.get("datetime") or c.get_text(" ", strip=True))
            for p in c["itemprop"].split():
                node.setdefault(p, val)
        return node

    return [walk(el) for el in soup.find_all(attrs={"itemscope": True})
            if el.find_parent(attrs={"itemscope": True}) is None]


def extract_structured_data(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    blocks, errors, nodes, entities, no_ctx = 0, [], [], [], []
    for s in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        blocks += 1
        raw = (s.string or s.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            try:  # common real-world issue: trailing commas / control chars
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("\n", " ").replace("\t", " ")), strict=False)
                errors.append("JSON-LD block has invalid JSON (recovered leniently; validators may reject it)")
            except ValueError as e:
                errors.append(f"JSON-LD block is not valid JSON: {e}")
                continue
        for item in (data if isinstance(data, list) else [data]):
            ctx = item.get("@context", "") if isinstance(item, dict) else ""
            if "schema.org" not in json.dumps(ctx):
                tmp: list = []
                _flatten(item, tmp)
                if tmp:
                    no_ctx += [t for n in tmp for t in _types(n)]
                continue
            _flatten(item, nodes)
            _entities(item, entities)
    if no_ctx:
        errors.append("JSON-LD block without a schema.org @context: types " + ", ".join(sorted(set(no_ctx))) +
                      " are not recognised by validators or AI crawlers")
    md_items, md_entities = [], []
    for it in _microdata(soup):
        _flatten(it, md_items)
        _entities(it, md_entities)
    return {"jsonld_blocks": blocks, "parse_errors": errors, "nodes": nodes, "microdata": md_items,
            "entities": entities + md_entities}


def _has(node: dict, spec: str) -> bool:
    return any(node.get(k) not in (None, "", [], {}) for k in spec.split("|"))


def validate_node(node: dict) -> dict | None:
    types = _types(node)
    rule_type = next((t for t in types if t in SCHEMA_RULES), None)
    if rule_type is None and "ProductGroup" in types:
        rule_type = "Product"
    if rule_type is None and any(t in ORG_TYPES for t in types):
        rule_type = "Organization"
    if rule_type is None:
        return None
    req, rec = SCHEMA_RULES[rule_type]
    missing_req = [r for r in req if not _has(node, r)]
    missing_rec = [r for r in rec if not _has(node, r)]
    # Product: Google requires at least one of offers / review / aggregateRating
    if rule_type == "Product" and "ProductGroup" not in types and not any(_has(node, k) for k in ("offers", "review", "aggregateRating")):
        missing_req.append("offers|review|aggregateRating")
    return {"type": rule_type, "declared_types": types, "missing_required": missing_req,
            "missing_recommended": missing_rec, "source": node.get("_source", "json-ld")}


def check_schema_page(url: str, html: str, page_kind: str) -> dict:
    sd = extract_structured_data(html)
    found = [v for n in sd["entities"] if (v := validate_node(n))]
    types = sorted({t for n in sd["nodes"] for t in _types(n)})
    md_types = sorted({t for n in sd["microdata"] for t in _types(n)})
    want = ["Product", "Offer"] if page_kind == "product" else ["Organization"]
    present = {f["type"] for f in found}
    if page_kind == "product":
        offers_ok = any(f["type"] in ("Offer", "AggregateOffer") for f in found)
        present |= {"Offer"} if offers_ok else set()
    missing_types = [t for t in want if t not in present]
    has_rating = any("AggregateRating" in _types(n) for n in sd["nodes"] + sd["microdata"])
    grouped: dict = {}
    for f in found:  # collapse identical variant entries (ProductGroup pages can have 100+)
        k = (f["type"], tuple(f["declared_types"]), tuple(f["missing_required"]), tuple(f["missing_recommended"]), f["source"])
        if k in grouped:
            grouped[k]["count"] += 1
        else:
            grouped[k] = {**f, "count": 1}
    return {"url": url, "kind": page_kind, "jsonld_blocks": sd["jsonld_blocks"], "jsonld_types": types,
            "microdata_types": md_types, "parse_errors": sd["parse_errors"], "items": list(grouped.values()),
            "entity_count": len(found),
            "missing_types": missing_types, "has_aggregate_rating": has_rating}


# ---------------------------------------------------------------- llms.txt + sitemap
def check_llms_txt(sess: PoliteSession, root: str) -> dict:
    out = {}
    for name in ("llms.txt", "llms-full.txt"):
        f = sess.get(urljoin(root, name), ua=BROWSER_UA, ua_label="discovery", accept="text/plain,text/markdown")
        looks_html = "<html" in f.text[:400].lower() or "<!doctype" in f.text[:400].lower()
        exists = bool(f.ok and not looks_html and f.text.strip())
        out[name] = {"exists": exists, "status": f.status, "final_url": f.final_url,
                     "bytes": len(f.text) if exists else 0,
                     "has_h1": exists and f.text.lstrip().startswith("# "),
                     "links": len(re.findall(r"\]\((https?://[^)]+)\)", f.text)) if exists else 0,
                     "platform_default": exists and ("# Agent Instructions" in f.text[:300] or "shop.com/SKILL.md" in f.text)}
    return out


def check_sitemap(sess: PoliteSession, root: str, robots_sitemaps: list[str]) -> dict:
    url = robots_sitemaps[0] if robots_sitemaps else urljoin(root, "sitemap.xml")
    f = sess.get(url, ua=BROWSER_UA, ua_label="discovery", accept="application/xml")
    return {"url": url, "status": f.status, "exists": f.ok and "<" in f.text[:200],
            "listed_in_robots": bool(robots_sitemaps)}


# ---------------------------------------------------------------- JS-rendered structured data
def render_dom(url: str, timeout: int = 25) -> str | None:
    """Return the DOM after JavaScript runs (headless Chrome), or None if Chrome is unavailable.
    Used only when the raw HTML lacks Product data, to tell 'missing' apart from 'JS-only'."""
    import os
    import shutil
    import subprocess
    if os.environ.get("AIV_NO_RENDER") == "1":
        return None
    chrome = next((shutil.which(c) for c in (os.environ.get("AIV_CHROME") or "", "google-chrome", "chromium",
                                            "chromium-browser") if c and shutil.which(c)), None)
    if not chrome:
        return None
    try:
        out = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--blink-settings=imagesEnabled=false",
                              f"--user-agent={BROWSER_UA}", "--virtual-time-budget=6000", "--dump-dom", url],
                             capture_output=True, timeout=timeout, text=True)
        return out.stdout if out.returncode == 0 and out.stdout else None
    except (subprocess.SubprocessError, OSError):
        return None
