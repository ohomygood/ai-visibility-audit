"""Export the admin unlock log (and Stripe used-session ledger) to a dated copy. NOT scheduled.

Render's free disk is ephemeral, so the durable record is the `ADMIN_AUDIT {json}` stdout lines in Render's
logs; this is an optional extra for a host with a persistent disk, e.g. nightly cron:
  5 0 * * *  cd /srv/ai-visibility-audit && python3 scripts/export_admin_log.py --out /srv/backup/aiv
Usage: python3 scripts/export_admin_log.py [--out DIR]   (default: exports/)
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("AIV_DATA_DIR", ROOT / "data"))
FILES = {"admin_unlocks": DATA / "admin" / "unlocks.jsonl", "admin_security": DATA / "admin" / "security.jsonl",
         "stripe_used_sessions": DATA / "stripe" / "used_sessions.jsonl"}


def export(out: Path, stamp: str | None = None) -> list[Path]:
    stamp = stamp or time.strftime("%Y%m%d")
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for name, src in FILES.items():
        if src.exists():
            dst = out / f"{name}_{stamp}.jsonl"
            shutil.copyfile(src, dst)
            done.append(dst)
    return done


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "exports"))
    a = ap.parse_args()
    for p in export(Path(a.out)):
        print(p)
    sys.exit(0)
