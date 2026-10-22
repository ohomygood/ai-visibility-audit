"""Stripe Payment Link -> audit unlock.

Flow (no Stripe SDK, stdlib + requests):
  1. Buy buttons link to PAYMENT_LINK_URL?client_reference_id=<audit_id>[&prefilled_email=...].
  2. After payment Stripe redirects to <RENDER_URL>/paid?session_id={CHECKOUT_SESSION_ID}.
  3. /paid NEVER trusts the session_id: it retrieves the Checkout Session from the Stripe API with
     STRIPE_RESTRICTED_KEY (read-only key, "Checkout Sessions: Read") and checks:
        status == complete, mode == payment, payment_status == paid,
        payment_link == AIV_STRIPE_PAYMENT_LINK_ID (plink_...),
        amount_total/currency == AIV_STRIPE_EXPECTED_AMOUNT/CURRENCY (1900 usd) in the integration
        currency (Adaptive Pricing: the local-currency amount is in presentment_details; sessions on
        old API versions carry it as currency/amount_total + currency_conversion, both handled),
        client_reference_id is a well-formed audit id,
        the session id has not been redeemed before (data/stripe/used_sessions.jsonl).
  4. The optional webhook (checkout.session.completed, STRIPE_WEBHOOK_SECRET) is a second route into
     the same redeem(): it re-fetches the session from the API too, so both routes share one ledger
     and a session unlocks exactly once.
Without STRIPE_RESTRICTED_KEY or AIV_STRIPE_PAYMENT_LINK_ID nothing unlocks.

Surviving a wiped disk (Render free): client_reference_id is "<audit_id>_<base32(store domain)>" (<= 200 chars,
Stripe's allowed alphabet), so a verified session alone says WHICH audit was paid and WHICH store to (re)generate
it for. /paid redirects to /order/<id>?s=<session_id>; when the local state is gone, /order re-verifies the
session with Stripe and rebuilds the unlock (and re-runs the report once). The session is bound to its audit id
by Stripe, so replaying it can only re-open the same order: harmless.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

from . import audit as _audit

DEFAULT_PAYMENT_LINK = "https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02"   # $19 one-off, Adaptive Pricing on
SESSION_RE = re.compile(r"^cs_(test|live)_[A-Za-z0-9]{10,200}$")
AUDIT_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
REF_RE = re.compile(r"^([0-9]{8}-[0-9]{6}-[0-9a-f]{6})(?:_([a-z2-7]{1,176}))?$")
HOST_RE = re.compile(r"^(?=.{1,110}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
API = "https://api.stripe.com/v1/checkout/sessions/"
_ledger_lock = threading.Lock()


def payment_link_url() -> str:
    return os.environ.get("AIV_STRIPE_PAYMENT_LINK") or DEFAULT_PAYMENT_LINK


def encode_domain(domain: str) -> str | None:
    d = (domain or "").lower().strip().rstrip(".")
    if not HOST_RE.match(d):
        return None
    return base64.b32encode(d.encode()).decode().rstrip("=").lower()


def decode_domain(b32: str) -> str | None:
    try:
        raw = base64.b32decode(b32.upper() + "=" * (-len(b32) % 8)).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return None
    return raw if HOST_RE.match(raw) else None


def make_ref(audit_id: str, domain: str | None = None) -> str:
    """client_reference_id: audit id, plus the store domain when it fits (it always does for real domains)."""
    if not AUDIT_ID_RE.match(audit_id or ""):
        raise ValueError("bad audit id")
    enc = encode_domain(domain) if domain else None
    ref = f"{audit_id}_{enc}" if enc else audit_id
    return ref if len(ref) <= 200 else audit_id


def parse_ref(ref: str) -> tuple[str, str | None] | None:
    m = REF_RE.match(ref or "")
    if not m:
        return None
    dom = decode_domain(m.group(2)) if m.group(2) else None
    if m.group(2) and not dom:
        return None
    return m.group(1), dom


def payment_url(audit_id: str, email: str | None = None, domain: str | None = None) -> str:
    """Buy-button URL that maps the payment back to the audit (and its store, for recovery after a wipe)."""
    url = f"{payment_link_url()}?client_reference_id={quote(make_ref(audit_id, domain))}"
    if email and EMAIL_RE.match(email):
        url += f"&prefilled_email={quote(email)}"
    return url


def restricted_key() -> str:
    return os.environ.get("STRIPE_RESTRICTED_KEY", "").strip()


def webhook_secret() -> str:
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or os.environ.get("AIV_STRIPE_WEBHOOK_SECRET") or "").strip()


def configured() -> bool:
    return bool(restricted_key() and os.environ.get("AIV_STRIPE_PAYMENT_LINK_ID", "").startswith("plink_"))


class PaymentError(Exception):
    """reason is a short machine code; http is the status to show the buyer."""
    def __init__(self, reason: str, http: int = 402, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.http, self.detail = reason, http, detail


def fetch_session(session_id: str, timeout: float = 15.0) -> dict:
    if not SESSION_RE.match(session_id or ""):
        raise PaymentError("bad_session_id", 400)
    key = restricted_key()
    if not key:
        raise PaymentError("not_configured", 503, "STRIPE_RESTRICTED_KEY missing")
    if not key.startswith("rk_"):
        # refuse full secret keys (sk_) and anything else: this server only ever needs read access.
        # Any rk_ key with Checkout Sessions: Read works, including one that also has other read-only
        # permissions (e.g. Charges, Balance transactions); only GET /v1/checkout/sessions/{id} is called.
        raise PaymentError("not_configured", 503, "use a restricted rk_ key, not a secret sk_ key")
    try:
        r = requests.get(API + session_id, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    except requests.RequestException as e:
        raise PaymentError("stripe_unreachable", 502, type(e).__name__) from None
    if r.status_code == 404:
        raise PaymentError("session_not_found", 404)
    if r.status_code in (401, 403):
        raise PaymentError("stripe_auth", 503, f"HTTP {r.status_code}: key lacks Checkout Sessions: Read?")
    if r.status_code != 200:
        raise PaymentError("stripe_error", 502, f"HTTP {r.status_code}")
    return r.json()


def _integration_amount(s: dict) -> tuple[int | None, str]:
    """(amount, currency) in the Payment Link's own currency, whatever the buyer paid in."""
    cc = s.get("currency_conversion")   # API versions before 2025-03-31 put the local currency at top level
    if isinstance(cc, dict) and cc.get("source_currency"):
        return cc.get("amount_total"), str(cc.get("source_currency")).lower()
    return s.get("amount_total"), str(s.get("currency") or "").lower()


def verify_session(s: dict, session_id: str | None = None) -> str:
    """Return the audit id if the session is a real, complete $19 purchase from our Payment Link."""
    return verify_session_ref(s, session_id)[0]


def verify_session_ref(s: dict, session_id: str | None = None) -> tuple[str, str | None]:
    """(audit_id, store domain or None) of a real, complete $19 purchase from our Payment Link."""
    if s.get("object") != "checkout.session" or (session_id and s.get("id") != session_id):
        raise PaymentError("session_mismatch", 400)
    link_id = os.environ.get("AIV_STRIPE_PAYMENT_LINK_ID", "")
    if not link_id.startswith("plink_"):
        raise PaymentError("not_configured", 503, "AIV_STRIPE_PAYMENT_LINK_ID missing")
    if s.get("payment_link") != link_id:
        raise PaymentError("wrong_payment_link", 402, str(s.get("payment_link")))
    if s.get("mode") != "payment" or s.get("status") != "complete" or s.get("payment_status") != "paid":
        raise PaymentError("not_paid", 402, f"{s.get('status')}/{s.get('payment_status')}")
    amount, cur = _integration_amount(s)
    want_amt = int(os.environ.get("AIV_STRIPE_EXPECTED_AMOUNT", "1900"))
    want_cur = os.environ.get("AIV_STRIPE_EXPECTED_CURRENCY", "usd").lower()
    if amount != want_amt or cur != want_cur:
        raise PaymentError("wrong_amount", 402, f"{amount} {cur}")
    parsed = parse_ref(s.get("client_reference_id") or "")
    if not parsed:
        raise PaymentError("no_audit_id", 400)
    return parsed


def ledger_path() -> Path:
    return _audit.DATA_DIR / "stripe" / "used_sessions.jsonl"


def _used(session_id: str) -> dict | None:
    p = ledger_path()
    if not p.exists():
        return None
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("session_id") == session_id:
            return rec
    return None


def redeem(session_id: str, source: str, ensure, expect_audit_id: str | None = None) -> dict:
    """Verify via the Stripe API, then make sure the order is unlocked. Idempotent and replay-safe.

    `ensure(audit_id, domain, source)` -> bool: unlocks the existing audit, or rebuilds the order from the
    domain when the local state was wiped; False if neither is possible. The used-session ledger records the
    first redemption; a replay (or a redemption after a wipe) can only re-open the same audit id, because the
    id comes from the session's client_reference_id, which Stripe stores and the buyer cannot change.
    Returns {"audit_id", "status": "unlocked" | "already_redeemed"}; raises PaymentError otherwise.
    """
    s = fetch_session(session_id)
    aid, domain = verify_session_ref(s, session_id)
    if expect_audit_id and aid != expect_audit_id:
        raise PaymentError("session_mismatch", 400, "session belongs to another order")
    # rebuilding a wiped/purged order is allowed for AIV_RECOVERY_DAYS (default 30) after purchase, so an old
    # bookmarked link can't regenerate a report forever; existing orders are re-opened regardless
    max_age = float(os.environ.get("AIV_RECOVERY_DAYS", "30")) * 86400
    created = s.get("created")
    if domain and isinstance(created, (int, float)) and time.time() - created > max_age:
        domain = None
    with _ledger_lock:
        prev = _used(session_id)
        ok = ensure(aid, domain, f"{source}:{session_id}")
        if not prev:
            rec = {"session_id": session_id, "audit_id": aid, "source": source, "at": int(time.time()),
                   "livemode": bool(s.get("livemode")), "audit_found": bool(ok)}
            ledger_path().parent.mkdir(parents=True, exist_ok=True)
            with ledger_path().open("a") as f:
                f.write(json.dumps(rec) + "\n")
    if not ok:
        # paid, but the audit is gone and the session carries no store domain: manual refund/re-run
        raise PaymentError("audit_not_found", 404, aid)
    return {"audit_id": aid, "status": "already_redeemed" if prev else "unlocked"}
