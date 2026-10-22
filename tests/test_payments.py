"""Stripe Payment Link -> /paid verification. The Stripe API is mocked; no network."""
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from aiv import payments, server

AID = "20261022-121500-abc123"
SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"
LINK = "plink_1TestLinkABC"
ENV = {"STRIPE_RESTRICTED_KEY": "rk_test_dummy_readonly", "AIV_STRIPE_PAYMENT_LINK_ID": LINK}


def session(**kw):
    s = {"id": SID, "object": "checkout.session", "mode": "payment", "status": "complete",
         "payment_status": "paid", "payment_link": LINK, "amount_total": 1900, "currency": "usd",
         "client_reference_id": AID, "livemode": False}
    s.update(kw)
    return s


def fake_full(runs, url, out_dir):
    """Stand-in for the paid full audit: records the call and writes the report like the real one."""
    runs.append(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.zh.html").write_text("full report")


class FakeResp:
    def __init__(self, code, obj):
        self.status_code, self._obj = code, obj

    def json(self):
        return self._obj


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_payment_url(self):
        self.assertEqual(payments.payment_url(AID),
                         f"https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02?client_reference_id={AID}")
        self.assertTrue(payments.payment_url(AID, "a+b@shop.com").endswith("&prefilled_email=a%2Bb%40shop.com"))
        self.assertNotIn("prefilled_email", payments.payment_url(AID, "not-an-email"))
        with self.assertRaises(ValueError):
            payments.payment_url("../etc")

    def test_ok(self):
        self.assertEqual(payments.verify_session(session(), SID), AID)

    def test_adaptive_pricing_presentment(self):
        # current API: integration currency stays usd/1900, buyer's CNY is in presentment_details
        s = session(presentment_details={"presentment_amount": 13700, "presentment_currency": "cny"})
        self.assertEqual(payments.verify_session(s, SID), AID)

    def test_adaptive_pricing_legacy_currency_conversion(self):
        s = session(amount_total=13700, currency="cny",
                    currency_conversion={"amount_total": 1900, "source_currency": "usd", "fx_rate": "7.2"})
        self.assertEqual(payments.verify_session(s, SID), AID)
        s["currency_conversion"]["amount_total"] = 100
        with self.assertRaises(payments.PaymentError):
            payments.verify_session(s, SID)

    def test_rejections(self):
        cases = {"not_paid": session(payment_status="unpaid"),
                 "not_paid ": session(status="open"),
                 "wrong_payment_link": session(payment_link="plink_other"),
                 "wrong_amount": session(amount_total=100),
                 "wrong_amount ": session(currency="eur"),
                 "no_audit_id": session(client_reference_id=None),
                 "session_mismatch": session(id="cs_test_someoneelse0000")}
        for reason, s in cases.items():
            with self.assertRaises(payments.PaymentError) as cm:
                payments.verify_session(s, SID)
            self.assertEqual(cm.exception.reason, reason.strip())

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": ""}):
            with self.assertRaises(payments.PaymentError) as cm:
                payments.fetch_session(SID)
            self.assertEqual(cm.exception.reason, "not_configured")
        with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": "sk_live_full_secret"}):
            with self.assertRaises(payments.PaymentError) as cm:
                payments.fetch_session(SID)
            self.assertEqual(cm.exception.reason, "not_configured")
        with mock.patch.dict(os.environ, {"AIV_STRIPE_PAYMENT_LINK_ID": ""}):
            with self.assertRaises(payments.PaymentError):
                payments.verify_session(session(), SID)

    def test_bad_session_id_never_hits_api(self):
        with mock.patch("aiv.payments.requests.get") as g:
            for bad in ("", "abc", "cs_test_x/../../v1/charges", "pi_123"):
                with self.assertRaises(payments.PaymentError):
                    payments.fetch_session(bad)
            g.assert_not_called()

    def test_fetch_uses_bearer_and_maps_errors(self):
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())) as g:
            payments.fetch_session(SID)
            url, = g.call_args.args
            self.assertEqual(url, "https://api.stripe.com/v1/checkout/sessions/" + SID)
            self.assertEqual(g.call_args.kwargs["headers"]["Authorization"], "Bearer rk_test_dummy_readonly")
        for code, reason in ((404, "session_not_found"), (401, "stripe_auth"), (500, "stripe_error")):
            with mock.patch("aiv.payments.requests.get", return_value=FakeResp(code, {})):
                with self.assertRaises(payments.PaymentError) as cm:
                    payments.fetch_session(SID)
                self.assertEqual(cm.exception.reason, reason)


class TestServerPaid(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name) / "data"
        d = data / "reports" / AID
        d.mkdir(parents=True)
        (d / "audit.json").write_text(json.dumps({"url": "https://shop.example"}))
        self.d = d
        for p in (mock.patch("aiv.server.DATA_DIR", data), mock.patch("aiv.audit.DATA_DIR", data)):
            p.start()
            self.addCleanup(p.stop)
        _cp = mock.patch("aiv.capacity.CAP", __import__("aiv.capacity", fromlist=["Capacity"]).Capacity())
        _cp.start()
        self.addCleanup(_cp.stop)
        __import__("aiv.server", fromlist=["_running"])._running.clear()
        self.runs = []
        p = mock.patch("aiv.server.run_audit", side_effect=lambda url, mode, out_dir: fake_full(self.runs, url, out_dir))
        p.start()
        self.addCleanup(p.stop)
        self.srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def get(self, path):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        try:
            r = urllib.request.build_opener(NoRedirect).open(self.base + path)
            return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def test_no_key_stays_locked(self):
        with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": "", "AIV_STRIPE_PAYMENT_LINK_ID": LINK}), \
             mock.patch("aiv.payments.requests.get") as g:
            code, _, body = self.get(f"/paid?session_id={SID}")
            self.assertEqual(code, 503)
            self.assertIn("not_configured", body)
            g.assert_not_called()
        self.assertFalse((self.d / "paid.json").exists())
        code, _, _ = self.get(f"/r/{AID}/full/report.zh.html")
        self.assertEqual(code, 402)

    def test_paid_flow_single_use(self):
        with mock.patch.dict(os.environ, ENV), \
             mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())) as g:
            code, h, _ = self.get(f"/paid?session_id={SID}")
            self.assertEqual(code, 303)
            self.assertEqual(h["Location"], f"/order/{AID}?s={SID}")
            g.assert_called_once()
            self.assertTrue((self.d / "paid.json").exists())
            time.sleep(0.2)
            (self.d / "full" / "report.zh.html").unlink()       # pretend it is still running
            server._running.add(AID)
            try:
                code, _, body = self.get(f"/order/{AID}")
                self.assertEqual(code, 200)
                self.assertIn("generating", body.lower())
                self.assertIn('http-equiv="refresh"', body)
            finally:
                server._running.discard(AID)
            (self.d / "full" / "report.zh.html").write_text("full report")
            time.sleep(0.2)
            # replay: re-verified via the API; it can only re-open the SAME audit (client_reference_id is fixed
            # by Stripe), no second ledger entry and no second full run while the report exists
            code, h, _ = self.get(f"/paid?session_id={SID}")
            self.assertEqual((code, h["Location"]), (303, f"/order/{AID}?s={SID}"))
            time.sleep(0.2)
            self.assertEqual(len(self.runs), 1)
            ledger = (Path(self.tmp.name) / "data" / "stripe" / "used_sessions.jsonl").read_text().splitlines()
            self.assertEqual(len(ledger), 1)
            self.assertEqual(json.loads(ledger[0])["audit_id"], AID)

    def test_order_ready(self):
        (self.d / "paid.json").write_text("{}")
        (self.d / "full").mkdir()
        (self.d / "full" / "report.zh.html").write_text("x")
        code, _, body = self.get(f"/order/{AID}")
        self.assertEqual(code, 200)
        self.assertIn(f"/r/{AID}/full/report.zh.html", body)

    def test_unpaid_session_rejected(self):
        with mock.patch.dict(os.environ, ENV), \
             mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session(payment_status="unpaid"))):
            code, _, body = self.get(f"/paid?session_id={SID}")
        self.assertEqual(code, 402)
        self.assertFalse((self.d / "paid.json").exists())
        self.assertFalse((Path(self.tmp.name) / "data" / "stripe" / "used_sessions.jsonl").exists())

    def test_unknown_audit(self):
        with mock.patch.dict(os.environ, ENV), \
             mock.patch("aiv.payments.requests.get",
                        return_value=FakeResp(200, session(client_reference_id="20200101-000000-000000"))):
            code, _, body = self.get(f"/paid?session_id={SID}")
        self.assertEqual(code, 404)
        self.assertIn("audit_not_found", body)

    def _webhook(self, path, obj, secret="whsec_test"):
        body = json.dumps({"type": "checkout.session.completed", "data": {"object": obj}}).encode()
        ts = int(time.time())
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers={"Stripe-Signature": f"t={ts},v1={sig}"})
        try:
            r = urllib.request.urlopen(req)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_webhook_then_redirect_unlocks_once(self):
        env = dict(ENV, STRIPE_WEBHOOK_SECRET="whsec_test")
        with mock.patch.dict(os.environ, env), \
             mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())) as g:
            # forged event body claiming "paid" is not trusted: API is consulted
            code, j = self._webhook("/stripe/webhook", {"id": SID, "payment_status": "paid", "client_reference_id": AID})
            self.assertEqual((code, j["unlocked"]), (200, True))
            g.assert_called_once()
            code, j = self._webhook("/api/stripe/webhook", {"id": SID})
            self.assertEqual(j["status"], "already_redeemed")
            code, h, _ = self.get(f"/paid?session_id={SID}")
            self.assertEqual(code, 303)
        time.sleep(0.2)
        self.assertEqual(len(self.runs), 1)

    def test_webhook_bad_sig_and_no_key(self):
        with mock.patch.dict(os.environ, dict(ENV, STRIPE_WEBHOOK_SECRET="whsec_test")):
            code, _ = self._webhook("/stripe/webhook", {"id": SID}, secret="whsec_wrong")
            self.assertEqual(code, 400)
        with mock.patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test", "STRIPE_RESTRICTED_KEY": ""}):
            code, j = self._webhook("/stripe/webhook", {"id": SID})
            self.assertEqual(code, 503)       # Stripe will retry once the key is configured
        self.assertFalse((self.d / "paid.json").exists())

    def test_dev_unlock_disabled_in_production(self):
        with mock.patch.dict(os.environ, {"AIV_DEV_UNLOCK": "1", "AIV_ENV": "production"}):
            code, _, _ = self.get(f"/dev/unlock/{AID}")
            self.assertEqual(code, 404)
        self.assertFalse((self.d / "paid.json").exists())


if __name__ == "__main__":
    unittest.main()
