"""Cost dashboard from logs/llm_calls.jsonl. Usage: python3 scripts/cost_report.py [--json]"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

LOG = Path(os.environ.get("AIV_LOG_DIR", Path(__file__).resolve().parents[1] / "logs")) / "llm_calls.jsonl"


def summarize():
    by_day, by_engine, by_audit = defaultdict(float), defaultdict(lambda: [0, 0.0, 0]), defaultdict(float)
    n = 0
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            n += 1
            c = r.get("cost_usd") or 0.0
            by_day[r["ts"][:10]] += c
            e = by_engine[f"{r.get('provider')}{' (simulated)' if r.get('simulated') else ''}"]
            e[0] += 1; e[1] += c; e[2] += 1 if r.get("error") else 0
            by_audit[r.get("audit_id")] += c
    return {"calls": n, "total_usd": round(sum(by_day.values()), 4),
            "by_day": {k: round(v, 4) for k, v in sorted(by_day.items())},
            "by_engine": {k: {"calls": v[0], "usd": round(v[1], 4), "errors": v[2]} for k, v in by_engine.items()},
            "audits": len(by_audit), "max_audit_usd": round(max(by_audit.values(), default=0), 4),
            "audits_over_1usd": [k for k, v in by_audit.items() if v > 1.0]}


if __name__ == "__main__":
    s = summarize()
    if "--json" in sys.argv:
        print(json.dumps(s, indent=1))
    else:
        print(f"LLM calls: {s['calls']}  total: ${s['total_usd']}  audits: {s['audits']}  max/audit: ${s['max_audit_usd']}")
        for k, v in s["by_engine"].items():
            print(f"  {k:28s} calls={v['calls']:5d}  ${v['usd']:.4f}  errors={v['errors']}")
        for k, v in s["by_day"].items():
            print(f"  {k}  ${v:.4f}")
        if s["audits_over_1usd"]:
            print("  OVER $1:", ", ".join(s["audits_over_1usd"]))
