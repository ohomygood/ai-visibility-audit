"""Signed order links for manual (WeChat / admin) unlocks, so they survive a wiped disk.

Stripe orders are recoverable from Stripe itself (see payments.py). Manual unlocks are not in Stripe, so
/admin/unlock returns an order link that carries its own proof:

    /order/<audit_id>?u=<base32(store domain)>.<HMAC-SHA256(secret, "aiv-order-v1|<audit_id>|<b32>")[:32]>

When the local state is gone, /order verifies the signature and rebuilds the unlock (and re-runs the report once)
for exactly that audit id + store. Only the server can mint links (secret never leaves the server); a tampered
id, domain or signature is rejected. Secret: AIV_ORDER_LINK_SECRET (>= 32 chars), or else derived from
ADMIN_TOKEN (so rotating ADMIN_TOKEN without setting AIV_ORDER_LINK_SECRET invalidates old links).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from . import payments

TOKEN_RE = re.compile(r"^([a-z2-7]{1,176})\.([A-Za-z0-9_-]{32})$")


def _secret() -> bytes | None:
    s = os.environ.get("AIV_ORDER_LINK_SECRET", "")
    if len(s) >= 32:
        return s.encode()
    t = os.environ.get("ADMIN_TOKEN", "")
    if len(t) >= 32:
        return hmac.new(t.encode(), b"aiv-order-link-v1", hashlib.sha256).digest()
    return None


def enabled() -> bool:
    return _secret() is not None


def _mac(key: bytes, audit_id: str, b32: str) -> str:
    d = hmac.new(key, f"aiv-order-v1|{audit_id}|{b32}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(d).decode().rstrip("=")[:32]


def sign(audit_id: str, domain: str) -> str | None:
    key, b32 = _secret(), payments.encode_domain(domain)
    if not key or not b32 or not payments.AUDIT_ID_RE.match(audit_id):
        return None
    return f"{b32}.{_mac(key, audit_id, b32)}"


def verify(audit_id: str, token: str) -> str | None:
    """Store domain if the token is a valid link for this audit id, else None."""
    key = _secret()
    m = TOKEN_RE.match(token or "")
    if not key or not m or not payments.AUDIT_ID_RE.match(audit_id or ""):
        return None
    if not hmac.compare_digest(_mac(key, audit_id, m.group(1)), m.group(2)):
        return None
    return payments.decode_domain(m.group(1))


def order_path(audit_id: str, domain: str | None) -> str:
    tok = sign(audit_id, domain) if domain else None
    return f"/order/{audit_id}" + (f"?u={tok}" if tok else "")


def public_base() -> str:
    """Absolute base for links handed to buyers (Render sets RENDER_EXTERNAL_URL)."""
    return (os.environ.get("AIV_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
