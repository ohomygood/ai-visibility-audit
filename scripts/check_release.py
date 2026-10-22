#!/usr/bin/env python3
"""Fail if a release directory (the static site) contains a payment QR or WeChat payment copy.

  python3 scripts/check_release.py site/

Rules: the static site ships no images at all; no file is named like a QR / WeChat asset; no file is
byte-identical to the owner's real QR (if present on this machine, path in AIV_REAL_QR_PATH); no text file
mentions 微信 / WeChat / ¥ prices / a QR image. Exit 1 with the offending paths.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"}
NAME_RE = re.compile(r"qr|wechat|weixin|wxpay", re.I)
TEXT_RE = re.compile(r"微信|wechat|weixin|收款码|¥\s?\d|[\w/.-]*qr[\w-]*\.(?:jpe?g|png|webp|gif)", re.I)
REAL_QR = Path(os.environ.get("AIV_REAL_QR_PATH", "/workspace/payment/wechat_pay_qr.jpg"))


def problems(root: Path) -> list[str]:
    real = hashlib.sha256(REAL_QR.read_bytes()).hexdigest() if REAL_QR.is_file() else None
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        if real and hashlib.sha256(data).hexdigest() == real:
            out.append(f"{rel}: is the real payment QR")
        if NAME_RE.search(p.name):
            out.append(f"{rel}: QR/WeChat-like file name")
        if p.suffix.lower() in IMAGE_EXT:
            out.append(f"{rel}: image in the static site (none expected)")
        if p.suffix.lower() in (".html", ".js", ".css", ".txt", ".json", ".md") or p.name == "_headers":
            m = TEXT_RE.search(data.decode("utf-8", "replace"))
            if m:
                out.append(f"{rel}: WeChat/QR payment text: {m.group(0)!r}")
    return out


if __name__ == "__main__":
    bad = problems(Path(sys.argv[1] if len(sys.argv) > 1 else "site"))
    for b in bad:
        print("RELEASE CHECK:", b)
    sys.exit(1 if bad else 0)
