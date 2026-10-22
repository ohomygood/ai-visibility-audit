"""Bilingual (中文 / English) report rendering: HTML, Markdown and PDF."""
from __future__ import annotations

import html
import os
import shutil
import subprocess
from pathlib import Path

SITE_URL = os.environ.get("AIV_SITE_URL", "__AIV_DOMAIN__")

PART_LABELS = {
    "crawler_access": ("AI 爬虫能否访问", "AI crawler access"),
    "robots_txt": ("robots.txt 规则", "robots.txt rules"),
    "structured_data": ("结构化数据 (Schema)", "Structured data (schema)"),
    "llms_txt": ("llms.txt", "llms.txt"),
    "sitemap": ("站点地图", "Sitemap"),
}
SEV = {"high": ("高", "High", "#c62828"), "medium": ("中", "Medium", "#ef6c00"), "low": ("低", "Low", "#607d8b")}

e = html.escape



def _pay_url(audit_id: str, root: str | None = None) -> str:
    from urllib.parse import urlparse
    from .payments import payment_url
    try:
        return payment_url(audit_id, domain=urlparse(root or "").hostname)
    except ValueError:
        from .payments import payment_link_url
        return payment_link_url()


def bi(zh: str, en: str, tag: str = "span") -> str:
    return f'<{tag} class="zh">{zh}</{tag}><{tag} class="en">{en}</{tag}>'


def pct(x):
    return "–" if x is None else f"{x:.0%}"


CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
max-width:960px;margin:0 auto;padding:24px;color:#1d2433;line-height:1.55}
h1{font-size:26px;margin:0}h2{font-size:19px;margin-top:32px;border-bottom:2px solid #eef1f6;padding-bottom:6px}
.muted{color:#6b7385;font-size:13px}.card{background:#f7f9fc;border:1px solid #e3e8f0;border-radius:10px;padding:16px;margin:12px 0}
.score{font-size:54px;font-weight:700}.grade{font-size:20px;padding:2px 10px;border-radius:6px;background:#1d2433;color:#fff;margin-left:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:8px 0}th,td{border-bottom:1px solid #e8ecf2;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f2f5fa}.bar{height:8px;background:#e3e8f0;border-radius:4px;overflow:hidden}.bar>i{display:block;height:100%;background:#2e7d32}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;color:#fff;font-size:12px}
.ok{color:#2e7d32;font-weight:600}.bad{color:#c62828;font-weight:600}.warn{color:#ef6c00;font-weight:600}
.banner{background:#fff4e5;border:1px solid #ffcc80;padding:10px 14px;border-radius:8px;margin:12px 0}
.lock{background:linear-gradient(180deg,#f7f9fc,#e9eef7);border:1px dashed #9aa7bd;border-radius:10px;padding:18px;text-align:center}
.btn{display:inline-block;background:#1d4ed8;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600}
.toggle{float:right;font-size:13px}code{background:#eef1f6;padding:1px 5px;border-radius:4px;font-size:12.5px}
body.lang-zh .en{display:none}body.lang-en .zh{display:none}
@media print{.toggle,.btn{display:none}body{padding:0}}
"""


def _fmt_samples(s) -> str:
    if isinstance(s, dict):
        return " · ".join(f"{e(k)} ×{v}" for k, v in s.items())
    return f"×{s}"


def render_html(res: dict, lang: str = "zh") -> str:
    p, sc, vis = res["profile"], res["score"], res.get("visibility") or {}
    full = res["mode"] == "full"
    out = [f'<!doctype html><html lang="{ "zh-CN" if lang == "zh" else "en"}"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<title>{"[SIMULATED 模拟] " if vis.get("simulated") else ""}AI 可见度体检 · {e(p["domain"])}</title><style>{CSS}</style></head><body class="lang-{lang}">',
           '<div class="toggle"><a href="#" onclick="document.body.className=\'lang-zh\';return false">中文</a> · '
           '<a href="#" onclick="document.body.className=\'lang-en\';return false">English</a></div>',
           f'<h1>{bi("AI 可见度体检报告", "AI Visibility Audit")}</h1>',
           f'<p class="muted">{e(p["name"])} · <a href="{e(res["root"])}">{e(p["domain"])}</a> · '
           f'{bi("平台", "Platform")}: {e(p["platform"])} · {e(res["created_at"])} · '
           f'{bi("完整版" if full else "免费简检", "Full report" if full else "Free quick check")} · ID {e(res["audit_id"])}</p>']
    if vis.get("simulated"):
        out.append('<div class="banner">' + bi(
            "⚠️ AI 推荐测试使用的是<b>模拟引擎（stub）</b>，“AI 是否提到你”的数字仅作演示，不代表 ChatGPT / Gemini / Perplexity 的真实回答。接入真实 API 后会自动替换。技术检测部分（爬虫、robots、Schema、llms.txt）是真实结果。",
            "⚠️ The AI-recommendation test used a <b>simulated (stub) engine</b>. Mention rates are placeholders, not real ChatGPT / Gemini / Perplexity output. The technical checks (crawlers, robots, schema, llms.txt) are real.") + "</div>")
    # score card
    rows = "".join(
        f'<tr><td>{bi(*PART_LABELS[k])}</td><td style="width:45%"><div class="bar"><i style="width:{v["score"] / v["max"] * 100:.0f}%"></i></div></td>'
        f'<td>{v["score"]}/{v["max"]}</td></tr>' for k, v in sc["parts"].items())
    if res.get("site_unreachable"):
        out.append('<div class="banner">' + bi("⚠️ 无法正常访问你的首页，分数暂不评定，以下结果不完整。购买后若仍无法访问，将自动退款。",
                   "⚠️ Your homepage could not be loaded, so no score is given and results are incomplete. Paid audits that still can't reach the site are refunded automatically.") + "</div>")
    out.append(f'<div class="card"><span class="score">{sc["total"] if sc["total"] is not None else "–"}</span><span class="muted">/100</span>'
               f'<span class="grade">{sc["grade"]}</span> <span class="muted">{bi("技术就绪度", "Technical readiness")}</span>'
               f'<table>{rows}</table>'
               f'<p class="muted">{bi("AI 回答中提到你的比例", "Share of AI answers that mention you")}: <b>{pct(vis.get("overall_mention_rate"))}</b>'
               f' · {bi("品牌声量占比", "Share of voice")}: <b>{pct(vis.get("share_of_voice"))}</b></p></div>')
    # findings
    fs = res["findings"]
    shown = fs if full else fs[:6]
    out.append(f'<h2>{bi("发现的问题", "Findings")} ({len(fs)})</h2><table><tr><th>{bi("级别", "Severity")}</th>'
               f'<th>{bi("问题", "Issue")}</th><th>{bi("修复文件", "Fix file")}</th></tr>')
    for f in shown:
        zh, en_, color = SEV[f["severity"]]
        cnt = f' <span class="muted">×{f["count"]}</span>' if f.get("count", 1) > 1 else ""
        out.append(f'<tr><td><span class="pill" style="background:{color}">{bi(zh, en_)}</span></td>'
                   f'<td>{bi(e(f["zh"]), e(f["en"]))}{cnt}</td><td>{"<code>" + e(f["fix"]) + "</code>" if f.get("fix") else ""}</td></tr>')
    out.append("</table>")
    if not full and len(fs) > len(shown):
        out.append(f'<p class="muted">{bi(f"另有 {len(fs) - len(shown)} 项在完整报告中。", f"{len(fs) - len(shown)} more in the full report.")}</p>')
    # crawler table
    ba = res["bot_access"]
    out.append(f'<h2>{bi("AI 爬虫访问测试", "AI crawler access test")}</h2>'
               f'<p class="muted">{bi("用各家官方 User-Agent 请求首页和商品页。", "Homepage and product pages requested with each vendor’s official User-Agent.")}'
               f' CDN: {e(", ".join(ba["cdn"]) or "–")}</p><table><tr><th>Bot</th><th>{bi("结果", "Result")}</th><th>{bi("状态码", "HTTP status")}</th></tr>')
    for bot, v in ba["bots"].items():
        cls = {"ok": "ok", "partial": "warn", "blocked": "bad"}[v["verdict"]]
        lab = {"ok": ("可访问", "Allowed"), "partial": ("部分被拦", "Partly blocked"), "blocked": ("被拦截", "Blocked")}[v["verdict"]]
        if v["verdict"] == "ok" and v.get("rate_limited_pages"):
            cls, lab = "warn", ("可访问（有限流）", "Allowed (rate-limited)")
        codes = " ".join(str(r["status"] or "ERR") for r in v["pages"])
        out.append(f'<tr><td>{bot}</td><td class="{cls}">{bi(*lab)}</td><td><code>{codes}</code></td></tr>')
    out.append("</table>")
    # robots
    rb = res["robots"]
    out.append(f'<h2>robots.txt</h2>')
    if rb.get("exists") is False:
        out.append(f'<p>{bi("没有 robots.txt（默认全部允许）。", "No robots.txt (everything allowed by default).")}</p>')
    elif rb.get("tokens"):
        out.append(f'<table><tr><th>Token</th><th>{bi("类型", "Type")}</th><th>{bi("是否允许", "Allowed")}</th><th>{bi("依据", "Rule")}</th></tr>')
        for t, v in rb["tokens"].items():
            typ = bi("回答时抓取", "answer-time") if v.get("answer_time") else bi("训练", "training")
            rule = next(iter(v["detail"].values())) if v["detail"] else ""
            out.append(f'<tr><td>{t}</td><td>{typ}</td><td class="{"ok" if v["allowed"] else "bad"}">'
                       f'{"✓" if v["allowed"] else "✗"}</td><td class="muted">{e(rule)}</td></tr>')
        out.append("</table>")
    # schema
    out.append(f'<h2>{bi("结构化数据", "Structured data")}</h2><table><tr><th>{bi("页面", "Page")}</th><th>JSON-LD</th>'
               f'<th>Microdata</th><th>{bi("缺失", "Missing")}</th></tr>')
    for s in res["schema"]:
        miss = ", ".join(s["missing_types"]) or "–"
        if s.get("js_only_types"):
            miss += " (" + bi("仅 JS 渲染后出现：", "JS-only: ") + ", ".join(s["js_only_types"]) + ")"
        out.append(f'<tr><td><a href="{e(s["url"])}">{e(s["url"].replace(res["root"], "/"))}</a></td>'
                   f'<td>{e(", ".join(s["jsonld_types"]) or "–")}</td><td>{e(", ".join(s["microdata_types"]) or "–")}</td>'
                   f'<td class="{"bad" if s["missing_types"] else "ok"}">{e(miss)}</td></tr>')
    out.append("</table>")
    ll = res["llms_txt"]["llms.txt"]
    out.append(f'<h2>llms.txt</h2><p class="{"ok" if ll["exists"] else "bad"}">' +
               (bi(f"存在（{ll['bytes']} 字节，{ll['links']} 个链接）", f"Present ({ll['bytes']} bytes, {ll['links']} links)") +
                (bi("，但这是 Shopify 自动生成的通用文件", " — but it is Shopify's generic auto-generated file") if ll.get("platform_default") else "") if ll["exists"]
                else bi("不存在", "Missing")) + "</p>")
    # visibility
    out.append(f'<h2>{bi("AI 推荐测试", "AI recommendation test")}</h2>'
               f'<p class="muted">{len(vis.get("questions", []))} {bi("个买家问题", "buyer questions")} × {len(vis.get("engines", []))} '
               f'{bi("个引擎", "engines")} · {bi("每题采样", "samples per question")}: {_fmt_samples(vis.get("samples_per_question", 1))}</p>')
    out.append(_engines_used_html(res, bi, e))
    out.append(f'<table><tr><th>{bi("引擎", "Engine")}</th><th>{bi("提到你", "Mentioned you")}</th><th>{bi("比例", "Rate")}</th></tr>')
    for eng, v in vis.get("per_engine", {}).items():
        out.append(f'<tr><td>{e(eng)}{" (stub)" if v["simulated"] else ""}</td><td>{v["mentioned"]}/{v["answered"]}</td><td>{pct(v["rate"])}</td></tr>')
    out.append("</table>")
    if full:
        out.append(f'<h3>{bi("逐题结果", "Per question")}</h3><table><tr><th>#</th><th>{bi("问题", "Question")}</th>' +
                   "".join(f"<th>{e(x)}</th>" for x in vis["engines"]) + "</tr>")
        for q in vis["questions"]:
            out.append(f'<tr><td>{q["id"]}</td><td>{e(q["text"])}</td>' +
                       "".join(f'<td>{vis["per_question"].get(q["id"], {}).get(x, "–")}</td>' for x in vis["engines"]) + "</tr>")
        out.append("</table>")
        out.append(f'<h3>{bi("AI 推荐的竞品", "Brands AI recommends instead")}</h3><table><tr><th>{bi("品牌", "Brand")}</th><th>{bi("次数", "Mentions")}</th></tr>' +
                   "".join(f"<tr><td>{e(b)}</td><td>{n}</td></tr>" for b, n in vis["top_competitors"]) + "</table>")
        grounded = any("no live web search" not in (x.get("note") or "") for x in res.get("engines", []))
        out.append(f'<h3>{bi("AI 引用的来源网站（缺口）", "Sources AI cites (gaps)") if grounded else bi("AI 回答中给出链接的网站", "Websites linked in AI answers")}</h3>'
                   f'<p class="muted">{bi("这些站点在回答里被引用；争取被它们收录或评测，是提升推荐率最快的路径。", "Getting listed or reviewed on these sites is the fastest way to be recommended.")}'
                   f'{"" if vis.get("store_cited") or not vis["top_cited_domains"] else " " + bi("你的网站一次都没有被引用。", "Your own site was never cited.")}'
                   f'{"" if vis["top_cited_domains"] else " " + bi("本次引擎未联网搜索，没有引用来源数据。", "The engines in this run answered without live search, so there is no citation data.")}</p><table><tr><th>{bi("域名", "Domain")}</th><th>{bi("次数", "Citations")}</th></tr>' +
                   "".join(f"<tr><td>{e(d)}</td><td>{n}</td></tr>" for d, n in vis["top_cited_domains"]) + "</table>")
        fpk = res.get("fixpack", {})
        out.append(f'<h2>{bi("修复包", "Fix pack")}</h2><p>{bi("下载", "Download")}: <a href="fixpack.zip">fixpack.zip</a></p><ul>' +
                   "".join(f"<li><code>{e(k)}</code> <span class='muted'>{v} B</span></li>" for k, v in sorted(fpk.get("files", {}).items())) + "</ul>")
    else:
        out.append(f'<div class="lock"><h3>{bi("解锁完整报告 + 修复包：$19 美元，一次性；24 小时内交付", "Unlock the full report + fix pack: $19 USD one-off, delivered within 24 hours")}</h3>'
                   f'<p>{bi("25 个买家问题在 Google Gemini 上各采样 3 次（ChatGPT 和 Perplexity 即将支持）、竞品份额、AI 回答里链接到的网站，以及可直接粘贴的 robots.txt、JSON-LD、llms.txt、5 篇内容草稿和 Cloudflare 放行步骤。", "25 buyer questions sampled 3 times each on Google Gemini (ChatGPT and Perplexity coming soon), competitor share of voice, the websites AI answers link to, plus paste-ready robots.txt, JSON-LD, llms.txt, 5 content drafts and Cloudflare steps.")}</p>'
                   f'<p class="muted">{bi("按美元收费，Stripe 结账页可能显示本地货币金额；也可直接用美元支付，由发卡行换算。7 天内：未在 24 小时内交付或你认为报告没有帮助，可全额退款（每位客户限一次）。", "Charged in USD; Stripe checkout may show your local currency, or pay in USD and let your bank convert. Within 7 days: full refund if not delivered within 24 hours or if you found it not useful (once per customer).")} <a href="mailto:go_with_prob@mail.grokbot.com">go_with_prob@mail.grokbot.com</a></p>'
                   f'<a class="btn" href="{e(_pay_url(res["audit_id"], res.get("root")))}">{bi("银行卡解锁 $19 美元", "Unlock for $19 USD by card")}</a></div>')
    out.append(f'<h2>{bi("方法与隐私", "Method & privacy")}</h2><p class="muted">' + bi(
        f"只读检测：共 {res['request_count']} 个 GET 请求，每个域名每秒最多 1 个。AI 推荐测试只调用 Google Gemini 官方 API，不抓取任何 AI 网页端。原始数据 7 天后自动删除。爬取耗时 {res['crawl_seconds']} 秒，总耗时 {res['total_seconds']} 秒，AI 调用 {res['llm_calls']} 次，成本 ${res['llm_cost_usd']:.4f}。",
        f"Read-only: {res['request_count']} GET requests, at most 1 per second per domain. The AI test calls only the official Google Gemini API; we never scrape any AI web app. Raw data is deleted after 7 days. Crawl {res['crawl_seconds']}s, total {res['total_seconds']}s, {res['llm_calls']} AI calls, cost ${res['llm_cost_usd']:.4f}.") + "</p>")
    if vis.get("simulated"):
        out.append('<div class="banner">' + bi("本报告含模拟 AI 数据，仅供内部测试，不可作为公开样例。",
                                              "This report contains simulated AI data: internal test only, not a public sample.") + "</div>")
    out.append(f'<p class="muted">{e(SITE_URL)} · AI Visibility Audit v{e(res["version"])}</p></body></html>')
    return "\n".join(out)


def render_markdown(res: dict) -> str:
    p, sc, vis = res["profile"], res["score"], res.get("visibility") or {}
    L = ([f"> ⚠️ SIMULATED / 模拟数据: AI answers come from the stub engine, not real ChatGPT/Gemini/Perplexity. Internal test only, not a public sample.", ""]
         if vis.get("simulated") else []) + [f"# AI 可见度体检 / AI Visibility Audit — {p['name']} ({p['domain']})", "",
         f"- Mode: {res['mode']} · Platform: {p['platform']} · {res['created_at']} · ID {res['audit_id']}",
         f"- 技术就绪度 / Technical readiness: **{sc['total'] if sc['total'] is not None else 'N/A (site unreachable)'}/100 ({sc['grade']})**",
         f"- AI mention rate: **{pct(vis.get('overall_mention_rate'))}**" + (" (SIMULATED stub engine)" if vis.get("simulated") else ""),
         "- Engines used: " + "; ".join(f"{x.get('engine')} ({x.get('model') or x.get('name')}{', ' + x['note'] if x.get('note') else ''})" for x in res.get("engines", []))
         + ("; not included: " + "; ".join((res.get("engine_info") or {}).get("notes", [])) if (res.get("engine_info") or {}).get("notes") else ""),
         f"- Crawl {res['crawl_seconds']}s · total {res['total_seconds']}s · {res['request_count']} requests · LLM cost ${res['llm_cost_usd']:.4f}", "",
         "## Score", ""] + [f"- {PART_LABELS[k][1]}: {v['score']}/{v['max']}" for k, v in sc["parts"].items()]
    L += ["", "## Findings", ""] + [f"- [{f['severity']}] {f['en']}" + (f" ×{f['count']}" if f.get('count', 1) > 1 else "") +
                                     (f" → `{f['fix']}`" if f.get('fix') else "") for f in res["findings"]]
    L += ["", "## AI crawlers", ""] + [f"- {b}: {v['verdict']} ({' '.join(str(r['status']) for r in v['pages'])})"
                                       for b, v in res["bot_access"]["bots"].items()]
    return "\n".join(L) + "\n"


def find_chrome() -> str | None:
    for c in (os.environ.get("AIV_CHROME"), "google-chrome", "chromium", "chromium-browser"):
        if c and shutil.which(c):
            return shutil.which(c)
    return None


def html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = find_chrome()
    if not chrome:
        return False
    try:
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                       check=True, capture_output=True, timeout=60)
        return pdf_path.exists() and pdf_path.stat().st_size > 1000
    except (subprocess.SubprocessError, OSError):
        return False


def write_reports(res: dict, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    outs = {}
    for lang in ("zh", "en"):
        pth = out_dir / f"report.{lang}.html"
        pth.write_text(render_html(res, lang), encoding="utf-8")
        outs[f"html_{lang}"] = pth.name   # relative to the report dir (no server paths in audit.json)
    md = out_dir / "report.md"
    md.write_text(render_markdown(res), encoding="utf-8")
    outs["markdown"] = md.name
    if res["mode"] == "full" and os.environ.get("AIV_NO_PDF") != "1":
        for lang in ("zh", "en"):
            pdf = out_dir / f"report.{lang}.pdf"
            if html_to_pdf(out_dir / f"report.{lang}.html", pdf):
                outs[f"pdf_{lang}"] = pdf.name
    return outs


def _engines_used_html(res: dict, bi, e) -> str:
    """Say exactly which engines produced the numbers, which were skipped, and whether answers used live search."""
    engs = res.get("engines") or []
    info = res.get("engine_info") or {}
    items = []
    for x in engs:
        label = x.get("engine", "")
        if x.get("simulated"):
            label += " (stub / simulated)"
        elif x.get("model"):
            label += f" ({x['model']})"
        note = x.get("note") or ""
        if "no live web search" in note:
            note = bi("仅模型自身知识，未联网搜索", "model knowledge only, no live web search")
        elif note == "live Google Search grounding":
            note = bi("联网搜索（Google Search grounding）", "live Google Search grounding")
        items.append(e(label) + (f" — {note}" if note else ""))
    skipped = [n for n in info.get("notes", [])]
    html = f'<p class="muted"><b>{bi("本报告使用的引擎", "Engines used in this report")}:</b> ' + "; ".join(items) + "</p>"
    if skipped:
        html += '<p class="muted">' + bi("未包含", "Not included") + ": " + "; ".join(e(n) for n in skipped) + "</p>"
    return html
