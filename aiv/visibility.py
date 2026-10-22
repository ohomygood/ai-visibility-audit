"""Buyer-question sampling against AI engines and mention / share-of-voice analysis."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .crawl import StoreProfile
from .llm import BudgetExceeded, CostMeter, Provider

SYSTEM = ("You are a helpful shopping assistant. Answer the shopper's question the way you normally would: "
          "recommend specific brands or online stores by name, briefly say why, and include links when you can.")

STOP_BRANDS = {"amazon prime", "conclusion", "summary", "tips", "note", "price", "quality", "materials", "comfort",
               "pros", "cons", "why", "best for", "budget", "overall", "durability", "sizing", "shipping", "returns",
               "fit", "style", "value", "verdict", "key features", "considerations", "bottom line"}


# Shopper-level categories. The store's own wording (title + meta description + product types) picks one;
# Shopify product types alone are often too narrow ("Peel Off Lip Stain") or missing.
CATEGORY_VOCAB = [
    ("gym clothing", ("gym clothing", "gym wear", "workout clothes", "activewear", "athleisure", "leggings", "sports bra")),
    ("makeup", ("makeup", "cosmetics", "lipstick", "eyeshadow", "lip ", "mascara", "foundation", "palette")),
    ("skincare", ("skincare", "skin care", "serum", "moisturizer", "cleanser", "sunscreen")),
    ("coffee", ("coffee", "espresso", "roast", "beans")),
    ("tea", (" tea", "matcha", "loose leaf")),
    ("shoes", ("shoes", "sneakers", "footwear", "boots", "flats")),
    ("socks", ("socks",)),
    ("jewelry", ("jewelry", "jewellery", "necklace", "earrings", "rings", "bracelet")),
    ("bedding", ("bedding", "sheets", "duvet", "pillow")),
    ("swimwear", ("swimwear", "bikini", "swimsuit")),
    ("supplements", ("supplement", "protein powder", "vitamins")),
    ("pet supplies", ("dog ", "cat ", "pet ")),
    ("bags", ("backpack", "handbag", "bags", "luggage")),
    ("clothing", ("clothing", "apparel", "t-shirts", "dresses", "jeans")),
]


def _category(profile: StoreProfile) -> str:
    text = " " + " ".join([profile.name or "", getattr(profile, "title", "") or "", profile.description or "",
                           " ".join(profile.product_types or [])]).lower() + " "
    best, best_n = None, 0
    for cat, kws in CATEGORY_VOCAB:
        n = sum(text.count(k) for k in kws)
        if n > best_n:
            best, best_n = cat, n
    if best:
        return best
    if profile.product_types:
        return profile.product_types[0].lower()
    if profile.collections:
        h = profile.collections[0].rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        if h not in ("all", "frontpage", "new", "sale"):
            return h
    if profile.products and profile.products[0].title:
        return profile.products[0].title.lower()
    return "products"


def build_questions(profile: StoreProfile, n: int) -> list[dict]:
    brand = profile.name or profile.domain
    c_main = _category(profile)
    cats = list(dict.fromkeys([c_main] + [c.lower() for c in profile.product_types[:3]]))
    c0 = cats[0]
    prices = [float(p.price) for p in profile.products if re.fullmatch(r"\d+(\.\d+)?", p.price or "")]
    budget = int(round(max(prices) * 1.2, -1)) if prices else 100
    cur = profile.currency or "USD"
    core = [
        ("category", f"What are the best {c0} brands to buy online right now?"),
        ("category", f"Where can I buy high-quality {c0} online with international shipping?"),
        ("brand", f"Is {brand} a legit and good store? What do customers say?"),
        ("competitor", f"What are some good alternatives to {brand}?"),
        ("budget", f"Best {c0} under {budget} {cur} — which brands would you recommend?"),
    ]
    extra = []
    for c in cats:
        extra += [
            ("category", f"Which {c} brands have the best reviews in 2026?"),
            ("category", f"Recommend a direct-to-consumer {c} brand worth trying."),
            ("use_case", f"I want to buy {c} as a gift. Which online stores do you recommend?"),
            ("category", f"What are the most popular {c} brands on Shopify?"),
            ("values", f"Which {c} brands are the most sustainable or ethically made?"),
            ("value", f"What {c} brand offers the best value for money?"),
        ]
    extra += [
        ("brand", f"{brand} vs its competitors: which is better for {c0}?"),
        ("brand", f"Does {brand} ship internationally and what is its return policy?"),
        ("brand", f"What is {brand} known for?"),
        ("competitor", f"Brands similar to {brand} for {c0}?"),
    ]
    extra += [
        ("category", f"What are the top-rated online stores for {c0}?"),
        ("use_case", f"What {c0} would you recommend for everyday use?"),
        ("value", f"Which premium {c0} brands are actually worth the price?"),
        ("category", f"What new or up-and-coming {c0} brands should I know about?"),
        ("values", f"Which {c0} brands offer free returns and a good warranty?"),
        ("use_case", f"Best {c0} for travel — which brands?"),
        ("category", f"Which {c0} brands do Reddit users recommend most?"),
        ("budget", f"Affordable {c0} brands with good quality?"),
        ("use_case", f"What {c0} should a first-time buyer get?"),
        ("category", f"Which independent {c0} brands sell directly online instead of on Amazon?"),
    ]
    for p in profile.products[:6]:
        if p.title:
            extra.append(("product", f"Where can I buy a good {p.product_type.lower() or c0} like the {p.title}?"))
    seen, qs = set(), []
    for kind, q in core + extra:
        if q not in seen:
            seen.add(q)
            qs.append({"id": f"q{len(qs) + 1}", "kind": kind, "text": q})
        if len(qs) >= n:
            break
    return qs


def brand_variants(profile: StoreProfile) -> list[str]:
    v = {profile.name.strip()} if profile.name else set()
    d = profile.domain.split(".")[0]
    v |= {d, profile.domain}
    if profile.name:
        v.add(re.sub(r"[^a-z0-9]", "", profile.name.lower()))
    return sorted({x for x in v if len(x) >= 3}, key=len, reverse=True)


def mentions_store(text: str, citations: list[str], profile: StoreProfile) -> bool:
    low = text.lower()
    squashed = re.sub(r"[^a-z0-9]", "", low)
    for v in brand_variants(profile):
        vl = v.lower()
        if re.search(r"(?<![a-z0-9])" + re.escape(vl) + r"(?![a-z0-9])", low):
            return True
        if len(vl) >= 6 and " " not in vl and vl in squashed:
            return True
    return any(profile.domain in (urlparse(c).netloc or "") for c in citations)


HEADING_WORDS = {"why", "tip", "tips", "pros", "cons", "what", "where", "how", "love", "loves", "recommendation",
                 "recommendations", "combo", "best", "top", "overall", "ultimate", "quick", "complaints", "common",
                 "consider", "considerations", "alternative", "alternatives", "similar", "brands", "stores", "customers",
                 "shipping", "support", "budget", "ratio", "price", "prices", "option", "options", "picks", "pick",
                 "verdict", "keep", "mind", "people", "great", "standout", "takeaway", "bottom", "line", "faq", "note",
                 "international", "shopping", "category", "categories", "summary", "conclusion", "guide", "vs", "versus",
                 "here", "shop", "visit", "website", "link", "official"}
_MD_LINK = re.compile(r"\[([^\]\n]{1,80})\]\((https?://[^)\s]+)\)")


def extract_brands(text: str) -> list[str]:
    """Brand names from a markdown-ish answer: bold names, list-item leads and link texts.
    Markdown links are reduced to their text; section headings and generic phrases are dropped."""
    lines = []
    for ln in text.splitlines():
        if ln.lstrip().startswith("#") and not _MD_LINK.search(ln):
            continue  # "### Best for Bags & Leather Goods" is a heading, not a brand
        lines.append(_MD_LINK.sub(lambda m: m.group(1), ln))
    plain = "\n".join(lines)
    cands = re.findall(r"\*\*([^*\n]{2,60})\*\*", plain)
    cands += re.findall(r"^\s*(?:\d+[.)]|[-*•])\s+\[?([A-Z][\w&'’.\- ]{1,40}?)\]?\s*(?:[–—:(]| - )", plain, re.M)
    cands += [m.group(1) for m in _MD_LINK.finditer(text)]
    out, seen = [], set()
    for c in cands:
        c = re.sub(r"^(?:\d+[.)]\s*)", "", c)
        c = re.sub(r"[:–—\-]+$", "", c).strip().strip(".").strip()
        words = re.findall(r"[a-z0-9’'-]+", c.lower())
        if not (2 <= len(c) <= 40) or c.lower() in STOP_BRANDS or c.lower().startswith(("best ", "top ")):
            continue
        if not words or len(words) > 4 or any(w in HEADING_WORDS for w in words) or "(" in c or "$" in c or c.endswith("?"):
            continue
        if c[0].islower() or c[0] in "\"“'‘":
            continue
        k = c.lower()
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def brand_key(b: str) -> str:
    """Canonical key so "Rothy's" / "Rothy’s" / "ROTHY'S" count as one brand."""
    return re.sub(r"[^a-z0-9]", "", b.lower().replace("’", "'"))


def aggregate_competitors(rows: list[dict], store_l: set) -> tuple[Counter, dict]:
    comp, shown = Counter(), {}
    store_keys = {re.sub(r"[^a-z0-9]", "", v) for v in store_l if len(v) >= 4}
    for r in rows:
        for b in dict.fromkeys(r.get("brands", [])):
            k = brand_key(b)
            if not k or b.strip()[:1] in "\"“'‘" or any(sk and sk in k for sk in store_keys) or any(w in HEADING_WORDS for w in re.findall(r"[a-z]+", b.lower())):
                continue
            comp[k] += 1
            shown.setdefault(k, b.replace("’", "'"))
    return comp, shown


def cited_domains(text: str, citations: list[str]) -> list[str]:
    urls = list(citations) + re.findall(r"https?://[^\s)\]>\"']+", text)
    ds = [urlparse(u).netloc.lower().removeprefix("www.") for u in urls]
    return list(dict.fromkeys(d for d in ds if d and "vertexaisearch" not in d))


DEFAULT_FULL_SAMPLES = {"ChatGPT": 2, "Perplexity": 2, "*": 3}  # paid web-search engines x2, Gemini (free grounding) x3


def samples_for(engine: Provider, samples) -> int:
    """``samples`` is an int (same for every engine) or a dict keyed by engine label, with "*" as fallback."""
    if isinstance(samples, int):
        return samples
    return int(samples.get(engine.engine, samples.get("*", 3)))


def run_visibility(profile: StoreProfile, engines: list[Provider], n_questions: int, samples,
                   meter: CostMeter, workers: int = 6) -> dict:
    qs = build_questions(profile, n_questions)
    per_engine_samples = {e.engine: samples_for(e, samples) for e in engines}
    jobs = [(e, q, s) for e in engines for q in qs for s in range(per_engine_samples[e.engine])]
    est = sum(e.estimate_cost(1) for e, _, _ in jobs)

    def one(job):
        e, q, s = job
        try:
            r = meter.run(e, q["text"], "visibility", system=SYSTEM, web_search=True, sample=s,
                          store_hint=profile.name or profile.domain)
        except BudgetExceeded as ex:
            return {"engine": e.engine, "q": q["id"], "sample": s, "skipped": str(ex)}
        return {"engine": e.engine, "q": q["id"], "sample": s, "error": r.error, "simulated": r.simulated,
                "mentioned": mentions_store(r.text, r.citations, profile) if not r.error else None,
                "brands": extract_brands(r.text), "domains": cited_domains(r.text, r.citations),
                "excerpt": r.text[:600]}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(one, jobs))

    per_engine, per_q = {}, defaultdict(dict)
    store_l = {v.lower() for v in brand_variants(profile)}
    comp, doms = Counter(), Counter()
    for e in engines:
        er = [r for r in rows if r["engine"] == e.engine and r.get("mentioned") is not None]
        per_engine[e.engine] = {"answered": len(er), "mentioned": sum(r["mentioned"] for r in er),
                                "rate": (sum(r["mentioned"] for r in er) / len(er)) if er else None,
                                "errors": sum(1 for r in rows if r["engine"] == e.engine and (r.get("error") or r.get("skipped"))),
                                "simulated": e.simulated}
    for q in qs:
        for e in engines:
            rr = [r for r in rows if r["engine"] == e.engine and r["q"] == q["id"] and r.get("mentioned") is not None]
            per_q[q["id"]][e.engine] = f"{sum(r['mentioned'] for r in rr)}/{len(rr)}" if rr else "–"
    comp, shown = aggregate_competitors(rows, store_l)
    for r in rows:
        for d in r.get("domains", []):
            doms[d] += 1
    store_hits = sum(1 for r in rows if r.get("mentioned"))
    total_brand_mentions = store_hits + sum(comp.values())
    answered = [r for r in rows if r.get("mentioned") is not None]
    return {
        "questions": qs, "engines": [e.engine for e in engines], "samples_per_question": per_engine_samples,
        "simulated": any(e.simulated for e in engines), "estimated_cost_usd": round(est, 4),
        "per_engine": per_engine, "per_question": dict(per_q),
        "overall_mention_rate": (store_hits / len(answered)) if answered else None,
        "share_of_voice": (store_hits / total_brand_mentions) if total_brand_mentions else None,
        "top_competitors": [(shown[k], n) for k, n in comp.most_common(12)], "top_cited_domains": doms.most_common(15),
        "store_cited": any(profile.domain in d for d, _ in doms.items()),
        "rows": rows,
    }
