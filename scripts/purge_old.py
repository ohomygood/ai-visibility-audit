"""Data-retention job: delete customer data older than AIV_RETENTION_DAYS (default 7).

Deletes, under data/:
  reports/<audit_id>/   crawls, audit.json, HTML/PDF reports, fix packs, payment flags
  emails/               any stored customer emails / contact submissions
  uploads/, tmp/        anything else customer-supplied
LLM cost logs (logs/llm_calls.jsonl) hold no URLs or answer text, only audit id, model, tokens and cost;
lines older than the retention window are also pruned so audit ids don't linger.

Run daily, e.g. cron:  17 3 * * *  cd /path/ai-visibility-audit && python3 scripts/purge_old.py
Usage: python3 scripts/purge_old.py [--days N] [--dry-run]
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("AIV_DATA_DIR", ROOT / "data"))
LOGS = Path(os.environ.get("AIV_LOG_DIR", ROOT / "logs"))
TARGETS = ["reports", "emails", "uploads", "tmp"]


def newest_mtime(p: Path) -> float:
    if p.is_file():
        return p.stat().st_mtime
    ts = [f.stat().st_mtime for f in p.rglob("*")] + [p.stat().st_mtime]
    return max(ts)


def purge(days: float, dry: bool = False, now: float | None = None) -> list[str]:
    now = now or time.time()
    cutoff = now - days * 86400
    removed = []
    for t in TARGETS:
        base = DATA / t
        if not base.exists():
            continue
        for entry in base.iterdir():
            if newest_mtime(entry) < cutoff:
                removed.append(str(entry))
                if not dry:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    log = LOGS / "llm_calls.jsonl"
    if log.exists():
        keep, dropped = [], 0
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                ts = datetime.strptime(json.loads(line)["ts"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except (ValueError, KeyError):
                ts = now
            if ts >= cutoff:
                keep.append(line)
            else:
                dropped += 1
        if dropped:
            removed.append(f"{log} ({dropped} lines)")
            if not dry:
                log.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
    return removed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=float(os.environ.get("AIV_RETENTION_DAYS", 7)))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = purge(a.days, a.dry_run)
    print(("would remove" if a.dry_run else "removed"), len(r), "items")
    for x in r:
        print(" ", x)
    sys.exit(0)
