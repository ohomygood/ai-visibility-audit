"""Acceptance helper: run free quick checks on several real stores and record timing.
Usage: python3 scripts/batch_quick.py OUT_DIR domain1 domain2 ...   (sequential, 1 req/s per store)"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiv.audit import run_audit  # noqa: E402

out = Path(sys.argv[1])
rows = []
for d in sys.argv[2:]:
    t0 = time.monotonic()
    try:
        r = run_audit(d, "quick", out_dir=out / d)
        rows.append({"store": d, "platform": r["profile"]["platform"], "score": r["score"]["total"],
                     "findings": len(r["findings"]), "requests": r["request_count"], "seconds": r["total_seconds"],
                     "under_60s": r["total_seconds"] < 60, "simulated": r["simulated"],
                     "blocked_bots": [b for b, v in r["bot_access"]["bots"].items() if v["verdict"] != "ok"]})
    except Exception as e:  # noqa: BLE001
        rows.append({"store": d, "error": str(e), "seconds": round(time.monotonic() - t0, 1), "under_60s": False})
    print(json.dumps(rows[-1]), flush=True)
summary = {"run_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "stores": rows,
           "under_60s": f"{sum(r.get('under_60s', False) for r in rows)}/{len(rows)}"}
(out / "quick_timing.json").write_text(json.dumps(summary, indent=1))
print(summary["under_60s"])
