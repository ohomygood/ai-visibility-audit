"""Manual WeChat Pay option (Chinese buyers). Config only; no WeChat API is ever called.

The buyer scans the owner's personal QR, sends the audit id + payment note to the contact, and the cashier
unlocks with `python3 -m aiv admin-unlock` (docs/manual_unlock.md).

Env
  AIV_WECHAT_ENABLED            1 (default) / 0
  AIV_WECHAT_PRICE_CNY          129
  AIV_WECHAT_EARLYBIRD          0 (default) / 1  -> show the early-bird price instead
  AIV_WECHAT_EARLYBIRD_PRICE_CNY 99
  AIV_WECHAT_QR_URL             /wechat_pay_qr.jpg  (local copy in landing/, gitignored; see scripts/install_wechat_qr.sh)
  AIV_WECHAT_CONTACT            go_with_prob@mail.grokbot.com  (e-mail only; never a personal WeChat ID)
"""
from __future__ import annotations

import os


def config() -> dict:
    eb = os.environ.get("AIV_WECHAT_EARLYBIRD", "0") == "1"
    regular = int(os.environ.get("AIV_WECHAT_PRICE_CNY", "129"))
    early = int(os.environ.get("AIV_WECHAT_EARLYBIRD_PRICE_CNY", "99"))
    return {"enabled": os.environ.get("AIV_WECHAT_ENABLED", "1") == "1",
            "price": early if eb else regular, "regular": regular, "earlybird": eb,
            "qr_url": os.environ.get("AIV_WECHAT_QR_URL", "/wechat_pay_qr.jpg"),
            "contact": os.environ.get("AIV_WECHAT_CONTACT", "go_with_prob@mail.grokbot.com")}
