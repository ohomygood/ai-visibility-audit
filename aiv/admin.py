"""Manual (cashier) unlock for WeChat Pay and other off-Stripe payments.

Two entry points, one code path (unlock_audit):
  HTTP  POST /admin/unlock   Authorization: Bearer $ADMIN_TOKEN, JSON body (see validate())
  CLI   python3 -m aiv admin-unlock <audit_id> --by --reason [--channel --amount --currency --ref]
        (direct on the server's data dir, or --remote <URL> to call the endpoint with $ADMIN_TOKEN)

Security
  * ADMIN_TOKEN unset or shorter than 32 chars -> endpoint disabled (404).
  * Token compared in constant time (hmac.compare_digest over SHA-256 digests).
  * Lockout: 5 failed attempts per IP in 15 min -> that IP gets 429 for 15 min; 20 failures from all IPs
    -> endpoint locked for everyone for 15 min. Correct tokens are refused while locked.
  * The token is never logged or echoed; failures are logged with IP only.
  * Every attempt that passes auth is appended to data/admin/unlocks.jsonl (append-only, also no-ops) and
    printed to stdout as `ADMIN_AUDIT {json}` (survives in Render logs); failed auth too (IP only).

Surviving a wiped disk (Render free)
  * The response carries `order_url` (and `order_link` with the public base): /order/<id>?u=<signed>. Send that
    link to the buyer; it rebuilds the unlock + report by itself after a wipe (aiv/orders.py).
  * Each log record includes the store `domain`, so `python3 -m aiv admin-reapply <exported log> --remote <URL>`
    re-applies every logged unlock (idempotent).
  * Pass `url` (store URL or domain) to unlock an audit whose files are already gone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from . import audit as _audit

MIN_TOKEN_LEN = 32
FAIL_WINDOW = 900
FAIL_PER_IP = 5
FAIL_GLOBAL = 20
CHANNELS = {"wechat", "alipay", "bank", "stripe", "comp", "other"}
CURRENCIES = {"CNY", "USD", "HKD", "EUR"}
AUDIT_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")      # one line, no control chars
REF_RE = re.compile(r"^[A-Za-z0-9_\-.:/#]{1,100}$")

_fails: dict[str, deque] = defaultdict(deque)
_all_fails: deque = deque()
_lock = threading.Lock()


class AdminError(Exception):
    def __init__(self, msg: str, http: int = 400):
        super().__init__(msg)
        self.http = http


def token() -> str | None:
    t = os.environ.get("ADMIN_TOKEN", "")
    return t if len(t) >= MIN_TOKEN_LEN else None


def enabled() -> bool:
    return token() is not None


def _prune(q: deque, now: float):
    while q and now - q[0] > FAIL_WINDOW:
        q.popleft()


def locked(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    with _lock:
        _prune(_fails[ip], now)
        _prune(_all_fails, now)
        return len(_fails[ip]) >= FAIL_PER_IP or len(_all_fails) >= FAIL_GLOBAL


def check_auth(header: str, ip: str, now: float | None = None) -> None:
    """Raises AdminError(404 disabled / 429 locked / 401 bad token)."""
    want = token()
    if not want:
        raise AdminError("not found", 404)
    if locked(ip, now):
        raise AdminError("too many failed attempts, try again later", 429)
    got = header[7:] if header.startswith("Bearer ") else ""
    ok = hmac.compare_digest(hashlib.sha256(got.encode()).digest(), hashlib.sha256(want.encode()).digest())
    if not ok:
        now = now or time.time()
        with _lock:
            _fails[ip].append(now)
            _all_fails.append(now)
        _security_log({"event": "admin_auth_failed", "ip": ip})
        raise AdminError("unauthorized", 401)


def reset_lockout():
    with _lock:
        _fails.clear()
        _all_fails.clear()


def validate(body: dict) -> dict:
    aid = str(body.get("audit_id") or "")
    if not AUDIT_ID_RE.match(aid):
        raise AdminError("audit_id is required (YYYYMMDD-HHMMSS-xxxxxx)")
    out = {"audit_id": aid}
    for f in ("by", "reason"):
        v = str(body.get(f) or "").strip()
        if not TEXT_RE.match(v):
            raise AdminError(f"'{f}' is required (one line, max 200 chars)")
        out[f] = v
    ch = str(body.get("channel") or "other").lower()
    if ch not in CHANNELS:
        raise AdminError(f"channel must be one of {sorted(CHANNELS)}")
    out["channel"] = ch
    amt = body.get("amount")
    if amt not in (None, ""):
        try:
            amt = round(float(amt), 2)
        except (TypeError, ValueError):
            raise AdminError("amount must be a number") from None
        if not 0 <= amt <= 100000:
            raise AdminError("amount out of range")
    out["amount"] = amt if amt not in (None, "") else None
    cur = body.get("currency")
    cur = str(cur).upper() if cur else ("CNY" if ch in ("wechat", "alipay") else None)
    if cur and cur not in CURRENCIES:
        raise AdminError(f"currency must be one of {sorted(CURRENCIES)}")
    out["currency"] = cur
    ref = str(body.get("ref") or "").strip()
    if ref and not REF_RE.match(ref):
        raise AdminError("ref: letters, digits and _-.:/# only, max 100")
    out["ref"] = ref or None
    if ch in ("wechat", "alipay", "bank") and not ref:
        raise AdminError(f"ref (payment transaction/note reference) is required for channel {ch}")
    # optional: the store, to rebuild an audit whose files were wiped (Render free disk) and to sign the order link
    dom = str(body.get("url") or body.get("domain") or "").strip().lower()
    if dom:
        from urllib.parse import urlparse
        from .payments import HOST_RE
        host = urlparse(dom if "://" in dom else f"https://{dom}").hostname or ""
        if not HOST_RE.match(host):
            raise AdminError("url/domain: a public store domain, e.g. shop.example.com")
        out["domain"] = host
    return out


def log_path() -> Path:
    return _audit.DATA_DIR / "admin" / "unlocks.jsonl"


def _emit(rec: dict):
    """One JSON line on stdout so it survives in Render's log stream (ephemeral disk). Never carries secrets:
    records are built only from validated fields, and any secret value is scrubbed defensively."""
    line = json.dumps(rec, ensure_ascii=False)
    for k in ("ADMIN_TOKEN", "STRIPE_RESTRICTED_KEY", "STRIPE_WEBHOOK_SECRET", "AIV_STRIPE_WEBHOOK_SECRET"):
        v = os.environ.get(k)
        if v and len(v) >= 8:
            line = line.replace(v, "REDACTED")
    print("ADMIN_AUDIT " + line, flush=True)


def _append(rec: dict):
    _emit(rec)
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)   # append-only writes
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _security_log(rec: dict):
    _emit({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec})
    p = _audit.DATA_DIR / "admin" / "security.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}) + "\n")


def unlock_audit(fields: dict, source: str, wait: bool = False) -> dict:
    """Same unlocked state + report trigger as /paid. Idempotent; every call is logged (with the store domain, so
    the log alone can re-apply it after a wipe: reapply_from_log). Returns a server-signed order link the buyer can
    keep: it rebuilds the unlock by itself if the server's disk was wiped (aiv/orders.py)."""
    from .server import ensure_paid, order_meta, report_dir, store_domain
    from . import orders
    aid = fields["audit_id"]
    d = report_dir(aid)
    meta = order_meta(d)
    domain = store_domain(meta) or fields.get("domain")
    if meta is None and not domain:
        result = "not_found"
    elif (d / "paid.json").exists():
        result = "already_unlocked"
    else:
        result = "unlocked" if meta is not None else "rebuilt"
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **{k: v for k, v in fields.items() if k != "domain"},
           "domain": domain, "source": source, "result": result}
    if result in ("unlocked", "rebuilt", "already_unlocked"):
        pay = {k: fields.get(k) for k in ("channel", "amount", "currency", "ref", "by")}
        ok = ensure_paid(aid, domain, f"admin:{fields['channel']}:{fields.get('ref') or '-'}", wait=wait,
                         extra={"manual": pay})
        if not ok:
            rec["result"] = result = "not_found"
    _append(rec)
    if result == "not_found":
        raise AdminError("audit not found (expired/wiped): pass the store url/domain to rebuild it", 404)
    path = orders.order_path(aid, domain)
    out = {"audit_id": aid, "result": result, "order_url": path}
    if orders.public_base():
        out["order_link"] = orders.public_base() + path
    return out


def parse_log(lines) -> list[dict]:
    """Admin unlock records from an exported log: `ADMIN_AUDIT {json}` lines (Render logs) or plain JSONL."""
    out = []
    for line in lines:
        line = line.strip()
        j = line.find("{")
        if j < 0 or ("ADMIN_AUDIT" not in line[:j] and j != 0):
            continue
        try:
            rec = json.loads(line[j:])
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("audit_id") and rec.get("result") in ("unlocked", "rebuilt", "already_unlocked"):
            out.append(rec)
    return out


def reapply_from_log(lines, by: str, apply) -> list[dict]:
    """Re-apply every logged manual unlock once per audit id. `apply(fields)` performs it (locally or remotely)."""
    seen, results = set(), []
    for rec in parse_log(lines):
        aid = rec["audit_id"]
        if aid in seen:
            continue
        seen.add(aid)
        fields = {"audit_id": aid, "by": by, "reason": f"reapply after wipe: {str(rec.get('reason') or '')[:150]}",
                  "channel": rec.get("channel") or "other", "amount": rec.get("amount"),
                  "currency": rec.get("currency"), "ref": rec.get("ref"), "url": rec.get("domain")}
        try:
            results.append({"audit_id": aid, **apply(validate(fields))})
        except AdminError as e:
            results.append({"audit_id": aid, "result": "error", "error": str(e)})
    return results
