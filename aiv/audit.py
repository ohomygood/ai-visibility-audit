"""Audit orchestration: quick (free) and full (paid) audits."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .checks import (AI_BOTS, ANSWER_TIME, check_bot_access, check_llms_txt, check_robots, check_schema_page,
                     check_sitemap)
from .crawl import discover
from .http import BROWSER_UA, PoliteSession
from .llm import CostMeter, engines_from_env, select_engines  # noqa: F401
from .visibility import DEFAULT_FULL_SAMPLES, run_visibility

DATA_DIR = Path(os.environ.get("AIV_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))

BOT_WEIGHTS = {"OAI-SearchBot": 8, "ChatGPT-User": 7, "PerplexityBot": 7, "GPTBot": 4, "ClaudeBot": 4}


def _audit_id(domain: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(f"{domain}{time.time()}".encode()).hexdigest()[:6]


def score(res: dict) -> dict:
    parts = {}
    # 1. crawler access (30)
    bots = res["bot_access"]["bots"]
    got = sum(BOT_WEIGHTS[b] * (1 if v["verdict"] == "ok" else 0.5 if v["verdict"] == "partial" else 0)
              for b, v in bots.items())
    parts["crawler_access"] = (round(got / sum(BOT_WEIGHTS.values()) * 30), 30)
    # 2. robots (15)
    toks = res["robots"].get("tokens", {})
    if toks:
        w = {t: (2 if t in ANSWER_TIME else 1) for t in toks}
        ok = sum(w[t] for t, v in toks.items() if v["allowed"])
        parts["robots_txt"] = (round(ok / sum(w.values()) * 15), 15)
    else:
        parts["robots_txt"] = (8, 15)
    # 3. structured data (35)
    prod = [p for p in res["schema"] if p["kind"] == "product"]
    home = [p for p in res["schema"] if p["kind"] == "home"]
    sd = 0.0
    if prod:
        n = len(prod)
        sd += 10 * sum("Product" not in p["missing_types"] for p in prod) / n
        sd += 8 * sum("Offer" not in p["missing_types"] and not any(
            i["type"] in ("Offer", "AggregateOffer") and i["missing_required"] for i in p["items"]) for p in prod) / n
        sd += 5 * sum(p["has_aggregate_rating"] for p in prod) / n
        rec_scores = []
        for p in prod:
            pi = [i for i in p["items"] if i["type"] == "Product"]
            if pi:
                rec_scores.append(1 - len(pi[0]["missing_recommended"]) / 7)
        sd += 4 * (sum(rec_scores) / n if rec_scores else 0)
    if home and "Organization" not in home[0]["missing_types"]:
        sd += 5
    if not any(p["parse_errors"] for p in res["schema"]):
        sd += 3
    parts["structured_data"] = (round(sd), 35)
    # 4. llms.txt (10) + sitemap (10)
    ll = res["llms_txt"]["llms.txt"]
    parts["llms_txt"] = (0 if not ll["exists"] else 4 if ll.get("platform_default") else 10, 10)
    parts["sitemap"] = (10 if res["sitemap"]["exists"] and res["sitemap"]["listed_in_robots"] else
                        6 if res["sitemap"]["exists"] else 0, 10)
    total = sum(v[0] for v in parts.values())
    return {"total": total, "max": 100, "parts": {k: {"score": v[0], "max": v[1]} for k, v in parts.items()},
            "grade": "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 50 else "D"}


def findings(res: dict) -> list[dict]:
    """Human-readable issues, bilingual, each pointing at the fix file that resolves it."""
    out = []

    def add(sev, key, zh, en, fix=None):
        out.append({"severity": sev, "key": key, "zh": zh, "en": en, "fix": fix})

    ba = res["bot_access"]
    cf = "cloudflare" in ba["cdn"]
    for bot, v in ba["bots"].items():
        if v["verdict"] == "ok":
            continue
        codes = sorted({str(r["status"]) for r in v["pages"] if r["blocked"]})
        sev = "high" if bot in ANSWER_TIME or bot == "PerplexityBot" else "medium"
        zh = f"{bot} 访问被拦截（{v['blocked_pages']}/{v['total_pages']} 个页面，状态码 {', '.join(codes)}）"
        en = f"{bot} is blocked on {v['blocked_pages']}/{v['total_pages']} pages (HTTP {', '.join(codes)})"
        if cf:
            zh += "。站点在 Cloudflare 后面：最常见原因是 Bot Fight Mode / AI Scrapers 拦截。注意 Cloudflare 会按 IP 识别真实爬虫，本工具用的是官方 UA 而非官方 IP，请在 Cloudflare 后台「Security → Events」确认真实爬虫是否被拦。"
            en += ". The site is behind Cloudflare; the usual cause is Bot Fight Mode / 'Block AI Scrapers'. Cloudflare verifies real crawlers by IP and we probe with the official UA from a non-crawler IP, so confirm in Cloudflare → Security → Events."
        add(sev, f"bot_blocked:{bot}", zh, en, "cloudflare_allow_ai_crawlers.md" if cf else "robots.txt.snippet")
    if res.get("site_unreachable"):
        st = res["profile"]["homepage_status"]
        add("high", "unreachable", f"我们的检测服务器无法正常访问你的首页（HTTP {st}）。结果不完整：llms.txt、sitemap、结构化数据无法判断。"
            "这通常是全局防爬、地区限制或针对数据中心 IP 的限流；AI 爬虫很可能也会遇到同样的问题。",
            f"Our audit server could not load your homepage (HTTP {st}). Results are incomplete: llms.txt, sitemap and structured "
            "data could not be assessed. This usually means global bot protection, geo-blocking or rate limits on datacenter IPs, "
            "which AI crawlers are likely to hit too.", "cloudflare_allow_ai_crawlers.md")
    for bot, v in ba["bots"].items():
        par = [r for r in v["pages"] if r["content_parity"] is False]
        if par:
            add("medium", f"parity:{bot}", f"{bot} 看到的页面缺少结构化数据（和浏览器看到的不一致，{len(par)} 个页面）。",
                f"{bot} receives pages without the structured data browsers get ({len(par)} pages).")
    rb = res["robots"]
    for t, v in rb.get("tokens", {}).items():
        if not v["allowed"]:
            sev = "high" if v.get("answer_time") else "low"
            add(sev, f"robots:{t}", f"robots.txt 禁止 {t} 抓取 {', '.join(v['blocked_paths'])}" +
                ("（这会直接让 AI 搜索无法引用你的页面）" if v.get("answer_time") else "（仅影响模型训练，可按需保留）"),
                f"robots.txt disallows {t} on {', '.join(v['blocked_paths'])}" +
                (" (prevents AI search from citing you)" if v.get("answer_time") else " (training only; optional)"),
                "robots.txt.snippet")
    for p in res["schema"]:
        path = urlparse(p["url"]).path or "/"
        for e in p["parse_errors"]:
            if "@context" in e:
                ez = "JSON-LD 代码块缺少 schema.org 的 @context，其中的 " + e.split("types ", 1)[-1].split(" are ")[0] + \
                     " 不会被校验工具和 AI 爬虫识别"
            elif "recovered" in e:
                ez = "JSON-LD 代码块不是合法 JSON（我们宽松解析成功，但校验工具可能直接丢弃）"
            else:
                ez = "JSON-LD 代码块不是合法 JSON，整块被忽略"
            add("high", "jsonld_parse", f"{path}：{ez}", f"{path}: {e}", "jsonld/")
        js_only = p.get("js_only_types") or []
        if js_only:
            add("high", "js_only", f"{path} 的 {', '.join(js_only)} 结构化数据只在 JavaScript 执行后才出现；GPTBot、ClaudeBot、PerplexityBot 等多数 AI 爬虫不执行 JS，看不到它",
                f"{path}: {', '.join(js_only)} structured data only appears after JavaScript runs; most AI crawlers (GPTBot, ClaudeBot, PerplexityBot) don't execute JS and never see it",
                "shopify/aiv-product-jsonld.liquid")
        for t in p["missing_types"]:
            if t in js_only:
                continue
            add("high" if t in ("Product", "Offer") else "medium", f"missing:{t}",
                f"{path} 缺少 {t} 结构化数据", f"{path} has no {t} structured data",
                "jsonld/organization.jsonld" if t == "Organization" else "jsonld/")
        for i in p["items"]:
            if i["missing_required"]:
                add("high", f"req:{i['type']}", f"{path} 的 {i['type']} 缺少必填字段：{', '.join(i['missing_required'])}",
                    f"{path}: {i['type']} is missing required fields: {', '.join(i['missing_required'])}", "jsonld/")
        if p["kind"] == "product" and not p["has_aggregate_rating"]:
            add("medium", "no_rating", f"{path} 没有 AggregateRating（评分），AI 购物推荐常引用评分",
                f"{path} has no AggregateRating; AI shopping answers often cite ratings", "shopify/aiv-product-jsonld.liquid")
        pi = [i for i in p["items"] if i["type"] == "Product"]
        if pi and pi[0]["missing_recommended"]:
            add("low", "rec:Product", f"{path} 的 Product 建议补充：{', '.join(pi[0]['missing_recommended'])}",
                f"{path}: Product could add {', '.join(pi[0]['missing_recommended'])}", "jsonld/")
    if res["llms_txt"]["llms.txt"].get("platform_default"):
        add("low", "llms_txt_default", "/llms.txt 是 Shopify 自动生成的通用“代理购物说明”，没有你店铺自己的介绍、主推商品和政策链接",
            "/llms.txt is Shopify's auto-generated generic agent-shopping file, with no store-specific summary, key products or policy links",
            "llms.txt")
    for bot, v in ba["bots"].items():
        if v.get("rate_limited_pages"):
            add("low", f"rate_limited:{bot}", f"{bot} 在 {v['rate_limited_pages']} 个页面上被限流（HTTP 429，重试后仍是），不算拦截，但频繁 429 会让 AI 爬虫少抓页面",
                f"{bot} was rate-limited (HTTP 429 after a retry) on {v['rate_limited_pages']} pages: not a block, but frequent 429s reduce AI crawl coverage")
    if not res["llms_txt"]["llms.txt"]["exists"] and res["llms_txt"]["llms.txt"]["status"] in (200, 404, 410):
        add("medium", "llms_txt", "没有 /llms.txt：AI 助手缺少一份关于你店铺的简明说明",
            "No /llms.txt: AI assistants have no concise, curated summary of your store", "llms.txt")
    if not res["sitemap"]["exists"] and res["sitemap"]["status"] not in (200, 404, 410):
        pass  # fetch failed (429/403/timeout): inconclusive, don't claim it is missing
    elif not res["sitemap"]["exists"]:
        add("medium", "sitemap", "没有找到 sitemap.xml", "No sitemap.xml found")
    elif not res["sitemap"]["listed_in_robots"]:
        add("low", "sitemap_robots", "robots.txt 里没有声明 Sitemap", "robots.txt does not declare the sitemap", "robots.txt.snippet")
    vis = res.get("visibility")
    if vis and vis.get("overall_mention_rate") is not None:
        r = vis["overall_mention_rate"]
        if r < 0.3:
            add("high" if r < 0.1 else "medium", "low_visibility",
                f"在 {len(vis['questions'])} 个买家问题里，AI 只在 {r:.0%} 的回答中提到你" + ("（示例数据）" if vis["simulated"] else ""),
                f"AI mentioned you in only {r:.0%} of answers to {len(vis['questions'])} buyer questions" + (" (simulated)" if vis["simulated"] else ""),
                "content/")
    order = {"high": 0, "medium": 1, "low": 2}
    # de-duplicate identical keys on many pages: keep the first, count the rest
    seen, dedup = {}, []
    for f in sorted(out, key=lambda x: order[x["severity"]]):
        k = (f["key"], f["fix"]) if f["key"].startswith(("rec:", "no_rating", "missing:", "req:", "jsonld_parse", "js_only")) else (f["key"], f["zh"])
        if k in seen:
            seen[k]["count"] += 1
            continue
        f["count"] = 1
        seen[k] = f
        dedup.append(f)
    return dedup


def run_audit(url: str, mode: str = "quick", engines_spec: str | None = None, out_dir: Path | None = None,
              min_interval: float = 1.0, budget_usd: float = 1.10, samples=None, engine_mode: str | None = None) -> dict:
    t0 = time.monotonic()
    sess = PoliteSession(min_interval=min_interval)
    profile, home = discover(sess, url, n_products=3)
    root = f"{urlparse(profile.final_url).scheme}://{urlparse(profile.final_url).netloc}/"
    audit_id = _audit_id(profile.domain)
    products = profile.products[:3]
    # Start the AI recommendation test now, in parallel with the technical checks (it only needs the profile).
    engines, engine_info = select_engines(engines_spec, engine_mode, purpose=mode)
    meter = CostMeter(audit_id=audit_id, budget_usd=budget_usd)
    from concurrent.futures import ThreadPoolExecutor
    _pool = ThreadPoolExecutor(max_workers=1)
    if mode == "quick":
        vis_future = _pool.submit(run_visibility, profile, engines[:2], n_questions=5, samples=1, meter=meter)
    else:
        vis_future = _pool.submit(run_visibility, profile, engines[:3], n_questions=25,
                                  samples=samples if samples is not None else DEFAULT_FULL_SAMPLES, meter=meter)
    baseline = {profile.final_url: home}
    for p in products:
        baseline[p.url] = sess.get(p.url, ua=BROWSER_UA, ua_label="baseline")
    urls = list(baseline)
    res = {"audit_id": audit_id, "mode": mode, "version": __version__, "url": url, "root": root,
           "created_at": time.strftime("%Y-%m-%d %H:%M (UTC%z)"), "profile": profile.to_dict()}
    res["bot_access"] = check_bot_access(sess, urls, baseline)
    sample_paths = ["/"] + [urlparse(u).path for u in urls[1:]] + ["/collections/all"]
    res["robots"] = check_robots(sess, root, sample_paths)
    res["schema"] = [check_schema_page(u, f.text, "home" if i == 0 else "product")
                     for i, (u, f) in enumerate(baseline.items()) if f.ok]
    # JS-only structured data: most AI crawlers don't execute JavaScript, so data injected client-side is invisible to them
    from .checks import render_dom
    # Render only ONE page (keeps the free check under 60 s); product pages share a template, so apply the result to the rest.
    missing = [sp for sp in res["schema"] if sp["kind"] == "product" and
               ("Product" in sp["missing_types"] or "Offer" in sp["missing_types"])]
    if missing:
        dom = render_dom(missing[0]["url"])
        if dom:
            rendered = check_schema_page(missing[0]["url"], dom, "product")
            found_after_js = {t for t in ("Product", "Offer") if t not in rendered["missing_types"]}
            for i, sp in enumerate(missing):
                sp["js_only_types"] = [t for t in sp["missing_types"] if t in found_after_js]
                sp["js_check"] = "rendered" if i == 0 else "inferred_from_same_template"
            missing[0]["rendered_types"] = rendered["jsonld_types"] + rendered["microdata_types"]
    res["llms_txt"] = check_llms_txt(sess, root)
    res["sitemap"] = check_sitemap(sess, root, res["robots"].get("sitemaps", []))
    res["crawl_seconds"] = round(time.monotonic() - t0, 1)

    res["visibility"] = vis_future.result()
    _pool.shutdown()
    res["llm_cost_usd"] = round(meter.spent_usd, 4)
    res["llm_calls"] = meter.calls
    res["simulated"] = res["visibility"]["simulated"]
    res["engines"] = [{"name": e.name, "engine": e.engine, "simulated": e.simulated,
                       "model": ", ".join(f"{m} ×{n}" for m, n in getattr(e, "models_used", {}).items()) or getattr(e, "model", ""),
                       "note": getattr(e, "mode_note", "")} for e in engines]
    res["engine_info"] = engine_info
    res["site_unreachable"] = not home.ok
    res["score"] = score(res)
    if res["site_unreachable"]:
        res["score"].update({"total": None, "grade": "N/A", "note": "homepage unreachable from the audit server; score withheld"})
    res["findings"] = findings(res)
    res["requests"] = sess.log
    res["request_count"] = len(sess.log)

    out_dir = Path(out_dir) if out_dir else DATA_DIR / "reports" / audit_id
    out_dir.mkdir(parents=True, exist_ok=True)
    from .report import write_reports
    from .fixes import write_fixpack
    if mode == "full":
        res["fixpack"] = write_fixpack(res, profile, out_dir, engines[0], meter)
        res["llm_cost_usd"] = round(meter.spent_usd, 4)
        res["llm_calls"] = meter.calls
    res["total_seconds"] = round(time.monotonic() - t0, 1)
    res["outputs"] = write_reports(res, out_dir)
    (out_dir / "audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return res
