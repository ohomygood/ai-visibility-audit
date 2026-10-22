#!/usr/bin/env bash
# Copy the owner's WeChat Pay QR into landing/ as a LOCAL, gitignored file referenced by landing/config.js
# (WECHAT_PAY.QR_URL) and the report (AIV_WECHAT_QR_URL=/wechat_pay_qr.jpg). It is never committed.
# For a static deploy, upload this file alongside the landing page by hand.
set -euo pipefail
SRC="${1:-/workspace/payment/wechat_pay_qr.jpg}"
DST="$(cd "$(dirname "$0")/.." && pwd)/landing/wechat_pay_qr.jpg"
[ -f "$SRC" ] || { echo "QR not found: $SRC" >&2; exit 1; }
cp "$SRC" "$DST" && echo "copied -> $DST (gitignored)"
