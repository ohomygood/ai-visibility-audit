"""Local dev server (stdlib only): static landing page + free-check API + Stripe webhook stub.

NOT for public deployment as-is (no TLS, single process). Production plan: same handlers behind
Cloudflare / a small VPS once the domain + Stripe are approved.

Routes
  GET  /                         landing page (landing/)
  POST /api/check {"url": ...}   free quick audit, IP rate-limited  -> {audit_id, score, report_url}
  GET  /r/<id>/report.zh.html    report files (fixpack.zip + full report only after payment)
  GET  /api/status/<id>          {paid, full_ready}
  GET  /paid?session_id=cs_...   Payment Link success redirect. Retrieves the Checkout Session from the
                                 Stripe API (STRIPE_RESTRICTED_KEY, read-only), verifies it (see
                                 aiv/payments.py), unlocks client_reference_id once -> 303 /order/<id>
  GET  /order/<id>               "generating" page (auto-refresh) -> full report when ready
  POST /stripe/webhook           optional 2nd route (alias /api/stripe/webhook): verifies Stripe-Signature
                                 (STRIPE_WEBHOOK_SECRET); checkout.session.completed -> same API re-check
                                 and same used-session ledger, so a session unlocks exactly once
  GET  /dev/unlock/<id>          simulate payment; only when AIV_DEV_UNLOCK=1 and AIV_ENV != production
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import socket
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from html import escape
from urllib.parse import parse_qs, urlparse

from .audit import DATA_DIR, run_audit
from .crawl import normalize_url
from .http import is_public_host
from .llm import is_production
from . import admin, payments

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing"
FREE_PER_HOUR = int(os.environ.get("AIV_FREE_PER_HOUR", "3"))
_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()
_paid_lock = threading.Lock()
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def rate_ok(ip: str, now: float | None = None) -> bool:
    now = now or time.time()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= FREE_PER_HOUR:
            return False
        q.append(now)
        return True


def verify_stripe_signature(payload: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    """Stripe webhook signature scheme v1 (HMAC-SHA256 over 't.payload')."""
    try:
        parts = dict(kv.split("=", 1) for kv in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    sigs = [v for k, v in (kv.split("=", 1) for kv in header.split(",")) if k == "v1"]
    return any(hmac.compare_digest(expected, s) for s in sigs)


def report_dir(audit_id: str) -> Path:
    return DATA_DIR / "reports" / audit_id


def mark_paid_and_run_full(audit_id: str, source: str, wait: bool = False, extra: dict | None = None) -> bool:
    """Unlock + start the full audit once. Returns False if the audit doesn't exist (e.g. purged).

    Shared by /paid, the Stripe webhook, /admin/unlock and the admin-unlock CLI (wait=True: the CLI
    process would otherwise exit before the background job finishes)."""
    d = report_dir(audit_id)
    with _paid_lock:
        if not d.exists() or not (d / "audit.json").exists():
            return False
        if (d / "paid.json").exists():
            return True
        meta = json.loads((d / "audit.json").read_text())
        (d / "paid.json").write_text(json.dumps({"paid_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "source": source, **(extra or {})},
                                                ensure_ascii=False))

    def job():
        try:
            run_audit(meta["url"], mode="full", out_dir=d / "full")
        except Exception as e:  # noqa: BLE001
            (d / "full_error.txt").write_text(str(e))
    if wait:
        job()
    else:
        threading.Thread(target=job, daemon=True).start()
    return True


PAGE = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '{refresh}<title>{title}</title><style>body{{font:16px/1.6 system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;color:#1d2433}}'
        '.btn{{display:inline-block;background:#3b5bdb;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;margin:4px 8px 4px 0}}'
        '.muted{{color:#667}}code{{background:#f1f3f5;padding:1px 4px;border-radius:4px}}</style></head><body>{body}</body></html>')

PAY_ERRORS = {
    "not_configured": ("支付验证尚未开通，暂时无法解锁。你的付款不会丢失，请联系我们。", "Payment verification isn't switched on yet, so we can't unlock right now. Your payment is safe; please contact us."),
    "bad_session_id": ("支付链接参数无效。", "The payment reference in this link is invalid."),
    "session_not_found": ("没有找到这笔付款。", "We couldn't find this payment."),
    "not_paid": ("这笔付款尚未完成。完成付款后请重新打开此页面。", "This payment isn't complete yet. Open this page again once it is."),
    "wrong_payment_link": ("这笔付款不是本产品的订单。", "This payment isn't an order for this product."),
    "wrong_amount": ("付款金额与订单不符。", "The amount paid doesn't match the order."),
    "no_audit_id": ("这笔付款没有关联体检报告。请先做免费体检，再从报告页点击解锁。", "This payment isn't linked to a report. Run the free check first, then unlock from the report page."),
    "audit_not_found": ("付款已确认，但对应的体检记录已过期或不存在。我们会全额退款或免费重新生成，请联系我们。", "Payment confirmed, but the audit has expired or doesn't exist. We'll refund in full or re-run it free; please contact us."),
}


def page(title: str, body: str, refresh: int | None = None) -> bytes:
    r = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return PAGE.format(refresh=r, title=escape(title), body=body).encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "AIV/0.1"

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        """Static landing on another origin (GitHub/Cloudflare Pages) calls this API: allow ONLY AIV_CORS_ORIGINS."""
        allowed = [o.strip() for o in os.environ.get("AIV_CORS_ORIGINS", "").split(",") if o.strip()]
        origin = self.headers.get("Origin", "")
        if origin and origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _file(self, path: Path):
        if not path.is_file():
            return self._json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _html(self, code, body: bytes):
        self._send(code, body, "text/html; charset=utf-8")

    def _paid(self, query: str):
        sid = (parse_qs(query).get("session_id") or [""])[0]
        try:
            res = payments.redeem(sid, "paid-redirect", mark_paid_and_run_full)
        except payments.PaymentError as e:
            zh, en = PAY_ERRORS.get(e.reason, ("验证付款时出错，请稍后刷新重试。", "Something went wrong verifying the payment. Refresh to retry in a moment."))
            ref = f"<p class='muted'>Ref: <code>{escape(e.reason)}</code>{' · ' + escape(sid) if payments.SESSION_RE.match(sid) else ''}</p>"
            return self._html(e.http, page("Payment", f"<h1>付款验证 / Payment check</h1><p>{zh}</p><p>{en}</p>{ref}"))
        self.send_response(303)
        self.send_header("Location", f"/order/{res['audit_id']}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _order(self, aid: str):
        d = report_dir(aid)
        if not ID_RE.match(aid) or not (d / "audit.json").exists():
            return self._html(404, page("Not found", "<p>报告不存在或已过期（7 天）。/ Report not found or expired (7 days).</p>"))
        if not (d / "paid.json").exists():
            url = escape(payments.payment_url(aid))
            return self._html(402, page("Locked", f"<p>完整报告尚未解锁。/ The full report isn't unlocked yet.</p><a class='btn' href='{url}'>解锁 Unlock $19</a>"))
        if (d / "full" / "report.zh.html").exists():
            body = (f"<h1>完整报告已生成 / Your full report is ready</h1><a class='btn' href='/r/{aid}/full/report.zh.html'>中文报告</a>"
                    f"<a class='btn' href='/r/{aid}/full/report.en.html'>English report</a><a class='btn' href='/r/{aid}/full/fixpack.zip'>fixpack.zip</a>"
                    f"<p class='muted'>报告 ID / Report ID: <code>{aid}</code> · 7 天后自动删除，请及时下载。/ Deleted after 7 days, please download it.</p>")
            return self._html(200, page("Report ready", body))
        if (d / "full_error.txt").exists():
            return self._html(200, page("Error", f"<p>生成失败，我们会自动全额退款或重新生成。/ Generation failed; we'll refund in full or re-run it.</p><p class='muted'>Report ID: <code>{aid}</code></p>"))
        return self._html(200, page("Generating", "<h1>付款成功，正在生成完整报告 / Payment received, generating your full report</h1>"
                                   "<p>通常需要几分钟，本页每 10 秒自动刷新。/ This usually takes a few minutes; this page refreshes every 10 seconds.</p>"
                                   f"<p class='muted'>报告 ID / Report ID: <code>{aid}</code></p>", refresh=10))

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path in ("/health", "/healthz"):
            from .llm import resolve_mode
            try:
                engines = resolve_mode(None)[0]
            except (RuntimeError, ValueError) as e:
                engines = f"unavailable: {type(e).__name__}"
            return self._json(200, {"ok": True, "production": is_production(), "engines": engines,
                                    "payments_configured": payments.configured(), "admin_enabled": admin.enabled(),
                                    "dev_unlock": os.environ.get("AIV_DEV_UNLOCK") == "1" and not is_production()})
        if path == "/paid":
            return self._paid(u.query)
        if path.startswith("/order/"):
            return self._order(path.rsplit("/", 1)[-1])
        if path in ("/", "/index.html"):
            return self._file(LANDING / "index.html")
        if path.startswith("/r/"):
            parts = path.split("/")
            if len(parts) < 4 or not ID_RE.match(parts[2]):
                return self._json(404, {"error": "not found"})
            d = report_dir(parts[2])
            rel = "/".join(parts[3:])
            paid = (d / "paid.json").exists()
            if (rel.startswith("full/") or rel.endswith(".zip")) and not paid:
                return self._json(402, {"error": "payment required"})
            if ".." in rel:
                return self._json(400, {"error": "bad path"})
            return self._file(d / rel)
        if path.startswith("/api/status/"):
            aid = path.rsplit("/", 1)[-1]
            if not ID_RE.match(aid):
                return self._json(404, {"error": "not found"})
            d = report_dir(aid)
            return self._json(200, {"exists": d.exists(), "paid": (d / "paid.json").exists(),
                                    "full_ready": (d / "full" / "audit.json").exists(),
                                    "full_error": (d / "full_error.txt").exists()})
        if path.startswith("/dev/unlock/") and os.environ.get("AIV_DEV_UNLOCK") == "1" and not is_production():
            aid = path.rsplit("/", 1)[-1]
            if ID_RE.match(aid):
                mark_paid_and_run_full(aid, "dev-unlock")
                return self._json(200, {"ok": True, "audit_id": aid})
        safe = (LANDING / path.lstrip("/")).resolve()
        if str(safe).startswith(str(LANDING.resolve())):
            return self._file(safe)
        return self._json(404, {"error": "not found"})

    def _client_ip(self) -> str:
        if os.environ.get("AIV_TRUST_PROXY") == "1":      # Render / Cloudflare in front; set only when true
            # header list is configurable because each host differs (Render: X-Forwarded-For; Cloudflare: CF-Connecting-IP)
            for h in os.environ.get("AIV_CLIENT_IP_HEADERS", "X-Forwarded-For").split(","):
                v = (self.headers.get(h.strip()) or "").split(",")[0].strip()
                if v:
                    return v[:64]
        return self.client_address[0]

    def _admin_unlock(self, body: bytes):
        try:
            admin.check_auth(self.headers.get("Authorization", ""), self._client_ip())
            try:
                req = json.loads(body or b"{}")
                if not isinstance(req, dict):
                    raise ValueError
            except ValueError:
                raise admin.AdminError("bad json") from None
            res = admin.unlock_audit(admin.validate(req), source="endpoint")
        except admin.AdminError as e:
            return self._json(e.http, {"error": str(e)})
        return self._json(200, res)

    def do_POST(self):
        path = urlparse(self.path).path
        n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        body = self.rfile.read(n)
        if path == "/api/check":
            ip = self._client_ip()
            try:
                req = json.loads(body or b"{}")
                url = normalize_url(req.get("url", ""))
                email = req.get("email") if isinstance(req.get("email"), str) else None   # never stored
            except (ValueError, AttributeError):
                return self._json(400, {"error": "invalid url"})
            if not is_public_host(url):
                return self._json(400, {"error": "url must be a public website"})
            if not rate_ok(ip):
                return self._json(429, {"error": f"free checks are limited to {FREE_PER_HOUR} per hour"})
            res = run_audit(url, mode="quick", out_dir=None)
            return self._json(200, {"audit_id": res["audit_id"], "score": res["score"]["total"],
                                    "grade": res["score"]["grade"], "findings": len(res["findings"]),
                                    "seconds": res["total_seconds"],
                                    "report_url": f"/r/{res['audit_id']}/report.zh.html",
                                    "report_url_en": f"/r/{res['audit_id']}/report.en.html",
                                    "pay_url": payments.payment_url(res["audit_id"], email)})
        if path == "/admin/unlock":
            return self._admin_unlock(body)
        if path in ("/stripe/webhook", "/api/stripe/webhook"):
            secret = payments.webhook_secret()
            if not secret:
                return self._json(503, {"error": "stripe webhook not configured"})
            if not verify_stripe_signature(body, self.headers.get("Stripe-Signature", ""), secret):
                return self._json(400, {"error": "bad signature"})
            try:
                ev = json.loads(body)
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if ev.get("type") not in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
                return self._json(200, {"received": True, "ignored": ev.get("type")})
            sid = (ev.get("data", {}).get("object", {}) or {}).get("id", "")
            try:
                # even a signed event is re-checked against the API, through the same once-only ledger
                res = payments.redeem(sid, "webhook", mark_paid_and_run_full)
            except payments.PaymentError as e:
                # 5xx -> Stripe retries later (config/network); 200 for permanent rejections
                code = 503 if e.http >= 500 else 200
                return self._json(code, {"received": True, "unlocked": False, "reason": e.reason})
            return self._json(200, {"received": True, "unlocked": res["status"] == "unlocked", "status": res["status"]})
        return self._json(404, {"error": "not found"})


def make_server(host="127.0.0.1", port=8787) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def serve(host="127.0.0.1", port=8787):
    os.environ.setdefault("AIV_BLOCK_PRIVATE", "0" if os.environ.get("AIV_ALLOW_PRIVATE") == "1" else "1")
    srv = make_server(host, port)
    print(f"AI Visibility Audit dev server on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
