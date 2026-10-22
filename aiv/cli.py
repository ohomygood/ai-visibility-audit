"""Command line: python -m aiv quick|full <url> [--engines stub] [--out DIR]"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import run_audit


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aiv", description="AI 可见度体检 / AI Visibility Audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("quick", "full"):
        s = sub.add_parser(name, help=f"{name} audit")
        s.add_argument("url")
        s.add_argument("--engines", default=None, help="comma list: stub,gemini,openai_compat,openai,perplexity (env AIV_ENGINES)")
        s.add_argument("--engine-mode", default=None, choices=["gemini-only", "full"],
                       help="used when --engines/AIV_ENGINES is not set (env AIV_ENGINE_MODE; default gemini-only). quick is always gemini-only")
        s.add_argument("--out", default=None, help="output directory")
        s.add_argument("--interval", type=float, default=1.0, help="min seconds between requests to the store (>=0.5)")
        s.add_argument("--budget", type=float, default=1.10, help="max LLM spend in USD for this audit (default 1.10)")
        s.add_argument("--samples", type=int, default=None,
                       help="full mode: same sample count for every engine (default: ChatGPT 2, Perplexity 2, Gemini 3)")
    rr = sub.add_parser("rerender", help="re-score and re-render reports from an existing audit.json (no network)")
    rr.add_argument("dir")
    srv = sub.add_parser("serve", help="local dev server (landing page + free check API)")
    srv.add_argument("--port", type=int, default=8787)
    srv.add_argument("--host", default="127.0.0.1")
    au = sub.add_parser("admin-unlock", help="owner: unlock an audit manually (comps / support), logged to data/admin/unlocks.jsonl")
    au.add_argument("audit_id")
    au.add_argument("--by", required=True, help="who confirmed the payment")
    au.add_argument("--reason", required=True, help="why, e.g. 'comp for reviewer'")
    au.add_argument("--channel", default="comp", help="comp|stripe|other|wechat|alipay|bank (default comp)")
    au.add_argument("--amount", default=None)
    au.add_argument("--currency", default=None, help="default CNY for wechat/alipay")
    au.add_argument("--ref", default=None, help="payment transaction id / note (required for wechat/alipay/bank)")
    au.add_argument("--url", default=None, help="store URL/domain: needed only if the audit's files were wiped")
    au.add_argument("--remote", default=None, metavar="URL",
                    help="call <URL>/admin/unlock with $ADMIN_TOKEN instead of writing the local data dir")
    ra = sub.add_parser("admin-reapply", help="re-apply manual unlocks from an exported ADMIN_AUDIT log after a wipe")
    ra.add_argument("log", help="file with `ADMIN_AUDIT {json}` lines (Render logs) or data/admin/unlocks.jsonl")
    ra.add_argument("--by", required=True)
    ra.add_argument("--remote", default=None, metavar="URL", help="apply via <URL>/admin/unlock with $ADMIN_TOKEN")
    a = ap.parse_args(argv)
    if a.cmd == "admin-unlock":
        return _admin_unlock(a)
    if a.cmd == "admin-reapply":
        return _admin_reapply(a)
    if a.cmd == "rerender":
        from pathlib import Path
        from .audit import findings, score
        from .report import write_reports
        d = Path(a.dir)
        res = json.loads((d / "audit.json").read_text())
        res["score"] = score(res)
        if res.get("site_unreachable"):
            res["score"].update({"total": None, "grade": "N/A"})
        res["findings"] = findings(res)
        res["outputs"] = write_reports(res, d)
        (d / "audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps(res["outputs"], indent=1))
        return 0
    if a.cmd == "serve":
        from .server import serve
        return serve(a.host, a.port)
    res = run_audit(a.url, mode=a.cmd, engines_spec=a.engines, out_dir=a.out, engine_mode=a.engine_mode,
                    min_interval=max(0.5, a.interval), budget_usd=a.budget, samples=a.samples)
    summary = {"audit_id": res["audit_id"], "domain": res["profile"]["domain"], "platform": res["profile"]["platform"],
               "score": res["score"]["total"], "grade": res["score"]["grade"],
               "mention_rate": res["visibility"]["overall_mention_rate"], "simulated": res["visibility"]["simulated"],
               "findings": len(res["findings"]), "crawl_seconds": res["crawl_seconds"],
               "total_seconds": res["total_seconds"], "requests": res["request_count"],
               "llm_calls": res["llm_calls"], "llm_cost_usd": res["llm_cost_usd"], "outputs": res["outputs"]}
    from .audit import DATA_DIR
    out = Path(a.out) if a.out else DATA_DIR / "reports" / res["audit_id"]   # audit.json keeps paths relative to this dir
    summary["out_dir"] = str(out)
    if res.get("fixpack"):
        summary["fixpack"] = str(out / res["fixpack"]["zip"])
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


def _admin_unlock(a) -> int:
    from . import admin
    fields = {"audit_id": a.audit_id, "by": a.by, "reason": a.reason, "channel": a.channel,
              "amount": a.amount, "currency": a.currency, "ref": a.ref, "url": a.url}
    if a.remote:
        import requests
        tok = os.environ.get("ADMIN_TOKEN", "")
        if len(tok) < admin.MIN_TOKEN_LEN:
            print("ADMIN_TOKEN env var missing or shorter than 32 chars", file=sys.stderr)
            return 2
        try:
            r = requests.post(a.remote.rstrip("/") + "/admin/unlock", json=fields, timeout=60,
                              headers={"Authorization": f"Bearer {tok}"})
        except requests.RequestException as e:
            print(f"request failed: {type(e).__name__}", file=sys.stderr)
            return 1
        print(r.text)
        return 0 if r.status_code == 200 else 1
    try:
        res = admin.unlock_audit(admin.validate(fields), source="cli", wait=True)
    except admin.AdminError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


def _remote_unlock(base: str, fields: dict) -> dict:
    import requests
    from . import admin
    tok = os.environ.get("ADMIN_TOKEN", "")
    if len(tok) < admin.MIN_TOKEN_LEN:
        raise admin.AdminError("ADMIN_TOKEN env var missing or shorter than 32 chars")
    try:
        r = requests.post(base.rstrip("/") + "/admin/unlock", json=fields, timeout=60,
                          headers={"Authorization": f"Bearer {tok}"})
    except requests.RequestException as e:
        raise admin.AdminError(f"request failed: {type(e).__name__}") from None
    try:
        j = r.json()
    except ValueError:
        j = {"error": f"HTTP {r.status_code}"}
    if r.status_code != 200:
        raise admin.AdminError(str(j.get("error") or r.status_code))
    return j


def _admin_reapply(a) -> int:
    from . import admin
    with open(a.log, encoding="utf-8") as f:
        lines = f.readlines()
    if a.remote:
        apply = lambda fields: _remote_unlock(a.remote, fields)  # noqa: E731
    else:
        apply = lambda fields: admin.unlock_audit(fields, source="reapply", wait=False)  # noqa: E731
    res = admin.reapply_from_log(lines, a.by, apply)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if all(r.get("result") != "error" for r in res) else 1
