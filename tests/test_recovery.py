"""Paid state survives a wiped data dir (Render free: ephemeral disk). Stripe is mocked; no network."""
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from aiv import admin, orders, payments, server

AID = "20261022-121500-abc123"
OTHER = "20261022-121501-def456"
SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"
LINK = "plink_1TestLinkABC"
DOMAIN = "shop.example.com"
TOKEN = "t" * 40
ENV = {"STRIPE_RESTRICTED_KEY": "rk_test_dummy_readonly", "AIV_STRIPE_PAYMENT_LINK_ID": LINK,
       "ADMIN_TOKEN": TOKEN, "AIV_ORDER_LINK_SECRET": ""}


def session(**kw):
    s = {"id": SID, "object": "checkout.session", "mode": "payment", "status": "complete", "payment_status": "paid",
         "payment_link": LINK, "amount_total": 1900, "currency": "usd",
         "client_reference_id": payments.make_ref(AID, DOMAIN), "livemode": False}
    s.update(kw)
    return s


class FakeResp:
    def __init__(self, code, obj):
        self.status_code, self._obj = code, obj

    def json(self):
        return self._obj


class TestRef(unittest.TestCase):
    def test_roundtrip_and_limits(self):
        ref = payments.make_ref(AID, "www.Shop-Example.co.uk")
        self.assertRegex(ref, r"^[A-Za-z0-9_-]{1,200}$")          # Stripe's allowed alphabet + length
        self.assertEqual(payments.parse_ref(ref), (AID, "www.shop-example.co.uk"))
        self.assertEqual(payments.parse_ref(AID), (AID, None))      # pre-recovery refs still accepted
        longest = payments.make_ref(AID, "a" * 60 + "." + "b" * 45 + ".com")
        self.assertLessEqual(len(longest), 200)
        self.assertEqual(payments.make_ref(AID, "a" * 63 + "." + "b" * 63 + ".com"), AID)   # too long -> id only
        for bad in (AID + "_", AID + "_!!", AID + "_" + "a" * 177, "x" + AID, AID + "_mfrgg"):   # "mfrgg"="abc": no TLD
            self.assertIsNone(payments.parse_ref(bad), bad)
        self.assertIsNone(payments.encode_domain("127.0.0.1"))
        self.assertIn("client_reference_id=" + payments.make_ref(AID, DOMAIN), payments.payment_url(AID, domain=DOMAIN))

    def test_order_link_signing(self):
        with mock.patch.dict(os.environ, ENV):
            tok = orders.sign(AID, DOMAIN)
            self.assertEqual(orders.verify(AID, tok), DOMAIN)
            self.assertIsNone(orders.verify(OTHER, tok))                       # bound to the audit id
            b32, mac = tok.split(".")
            evil = payments.encode_domain("evil.example.com")
            self.assertIsNone(orders.verify(AID, f"{evil}.{mac}"))              # bound to the store
            self.assertIsNone(orders.verify(AID, f"{b32}.{'A' * 32}"))
            with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "u" * 40}):        # other server secret
                self.assertIsNone(orders.verify(AID, tok))
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "", "AIV_ORDER_LINK_SECRET": ""}):
            self.assertIsNone(orders.sign(AID, DOMAIN))
            self.assertFalse(orders.enabled())


class WipeBase(unittest.TestCase):
    delay = 0.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.d = self.data / "reports" / AID
        self.d.mkdir(parents=True)
        (self.d / "audit.json").write_text(json.dumps({"url": f"https://{DOMAIN}/collections/all",
                                                       "root": f"https://{DOMAIN}/"}))
        patches = [mock.patch("aiv.server.DATA_DIR", self.data), mock.patch("aiv.audit.DATA_DIR", self.data),
                   mock.patch("aiv.server.is_public_host", return_value=True), mock.patch.dict(os.environ, ENV)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        _cp = mock.patch("aiv.capacity.CAP", __import__("aiv.capacity", fromlist=["Capacity"]).Capacity())
        _cp.start()
        self.addCleanup(_cp.stop)
        __import__("aiv.server", fromlist=["_running"])._running.clear()
        self.runs = []

        def fake_full(url, mode, out_dir):
            self.runs.append(url)
            time.sleep(self.delay)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "report.zh.html").write_text("full report")
        p = mock.patch("aiv.server.run_audit", side_effect=fake_full)
        p.start()
        self.addCleanup(p.stop)
        server._order_fail.clear()
        admin.reset_lockout()
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

    def wipe(self):
        shutil.rmtree(self.data)          # what a Render free restart does to the data dir
        self.assertFalse(self.data.exists())

    def settle(self):
        for _ in range(100):
            if not server._running:
                return
            time.sleep(0.02)


class TestStripeRecovery(WipeBase):
    def test_paid_order_survives_wipe(self):
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())) as g:
            code, h, _ = self.get(f"/paid?session_id={SID}")
            self.assertEqual((code, h["Location"]), (303, f"/order/{AID}?s={SID}"))
            self.settle()
            self.assertEqual(self.runs, [f"https://{DOMAIN}/collections/all"])
            self.wipe()
            code, _, body = self.get(f"/order/{AID}")                # without proof: gone
            self.assertEqual(code, 404)
            code, _, body = self.get(f"/order/{AID}?s={SID}")        # the buyer's bookmarked link
            self.assertEqual(code, 200, body)
            self.settle()
            self.assertEqual(self.runs[-1], f"https://{DOMAIN}/")   # regenerated for the store in the session
            self.assertEqual(len(self.runs), 2)
            paid = json.loads((self.d / "paid.json").read_text())
            self.assertTrue(paid["recovered"])
            self.assertEqual(paid["source"], f"order-recover:{SID}")
            self.assertTrue(json.loads((self.d / "order.json").read_text())["recovered"])
            code, _, body = self.get(f"/order/{AID}?s={SID}")        # now served from disk: no Stripe call
            self.assertIn(f"/r/{AID}/full/report.zh.html", body)
            self.assertEqual(g.call_count, 2)
            code, _, _ = self.get(f"/r/{AID}/full/report.zh.html")
            self.assertEqual(code, 200)

    def test_paid_redirect_after_wipe_also_recovers(self):
        self.wipe()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())):
            code, h, _ = self.get(f"/paid?session_id={SID}")
        self.assertEqual(code, 303)
        self.settle()
        self.assertEqual(len(self.runs), 1)
        self.assertTrue((self.d / "full" / "report.zh.html").exists())

    def test_concurrent_reopen_runs_once(self):
        self.delay = 0.5
        self.wipe()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())):
            codes = []
            ts = [threading.Thread(target=lambda: codes.append(self.get(f"/order/{AID}?s={SID}")[0])) for _ in range(6)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(codes, [200] * 6)
            self.settle()
            time.sleep(0.6)
            self.assertEqual(len(self.runs), 1)
            self.get(f"/order/{AID}?s={SID}")
            time.sleep(0.1)
            self.assertEqual(len(self.runs), 1)
        ledger = (self.data / "stripe" / "used_sessions.jsonl").read_text().splitlines()
        self.assertEqual(len(ledger), 1)

    def test_replay_on_other_order_rejected(self):
        self.wipe()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session())):
            code, _, body = self.get(f"/order/{OTHER}?s={SID}")
        self.assertEqual(code, 400)
        self.assertIn("session_mismatch", body)
        self.assertFalse((self.data / "reports" / OTHER).exists())
        self.assertFalse((self.data / "reports" / AID / "paid.json").exists())
        self.assertEqual(self.runs, [])

    def test_old_ref_without_domain_after_wipe(self):
        self.wipe()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session(client_reference_id=AID))):
            code, _, body = self.get(f"/order/{AID}?s={SID}")
        self.assertEqual(code, 404)
        self.assertIn("audit_not_found", body)

    def test_rebuild_window(self):
        self.wipe()
        old = session(created=int(time.time()) - 31 * 86400)
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, old)):
            code, _, body = self.get(f"/order/{AID}?s={SID}")
        self.assertEqual(code, 404)
        self.assertIn("audit_not_found", body)
        server._order_fail.clear()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session(created=int(time.time()) - 86400))):
            code, _, _ = self.get(f"/order/{AID}?s={SID}")
        self.assertEqual(code, 200)

    def test_unpaid_session_cannot_rebuild_and_is_throttled(self):
        self.wipe()
        with mock.patch("aiv.payments.requests.get", return_value=FakeResp(200, session(payment_status="unpaid"))) as g:
            code, _, _ = self.get(f"/order/{AID}?s={SID}")
            self.assertEqual(code, 402)
            code, _, body = self.get(f"/order/{AID}?s={SID}")
            self.assertEqual(code, 503)
            self.assertIn("retry_later", body)
            self.assertEqual(g.call_count, 1)
        self.assertFalse(self.data.joinpath("reports", AID, "paid.json").exists())

    def test_no_stripe_key_cannot_rebuild(self):
        self.wipe()
        with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": ""}), mock.patch("aiv.payments.requests.get") as g:
            code, _, _ = self.get(f"/order/{AID}?s={SID}")
            g.assert_not_called()
        self.assertEqual(code, 503)
        self.assertEqual(self.runs, [])


BODY = {"audit_id": AID, "by": "owner", "reason": "WeChat ¥129 confirmed", "channel": "wechat",
        "amount": 129, "currency": "CNY", "ref": "4200001234202610220000"}


class TestManualRecovery(WipeBase):
    def unlock(self, body=BODY):
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = admin.unlock_audit(admin.validate(body), source="endpoint")
        self.settle()
        return res, buf.getvalue().splitlines()

    def test_signed_order_link_survives_wipe(self):
        res, _ = self.unlock()
        self.assertEqual(res["result"], "unlocked")
        link = res["order_url"]
        self.assertTrue(link.startswith(f"/order/{AID}?u="))
        self.wipe()
        code, _, _ = self.get(link.split("?")[0])
        self.assertEqual(code, 404)
        code, _, body = self.get(link)
        self.assertEqual(code, 200, body)
        self.settle()
        self.assertTrue((self.d / "paid.json").exists())
        self.assertEqual(json.loads((self.d / "order.json").read_text())["url"], f"https://{DOMAIN}/")
        self.assertEqual(len(self.runs), 2)

    def test_tampered_or_foreign_link_rejected(self):
        res, _ = self.unlock()
        tok = res["order_url"].split("u=")[1]
        self.wipe()
        for path in (f"/order/{OTHER}?u={tok}", f"/order/{AID}?u={tok[:-1]}A",
                     f"/order/{AID}?u={payments.encode_domain('evil.example.com')}.{tok.split('.')[1]}"):
            code, _, body = self.get(path)
            self.assertEqual(code, 403, path)
            self.assertIn("bad_link", body)
        self.assertFalse(self.data.joinpath("reports", OTHER).exists())
        self.assertFalse(self.data.joinpath("reports", AID, "paid.json").exists())

    def test_reapply_from_exported_log(self):
        _, lines = self.unlock()
        self.assertTrue(any(line.startswith("ADMIN_AUDIT ") and DOMAIN in line for line in lines))
        render_log = [f"2026-10-22T05:00:00Z {line}" if i % 2 else line for i, line in enumerate(lines)]
        render_log += ["unrelated noise", "ADMIN_AUDIT {not json", 'ADMIN_AUDIT {"event": "admin_auth_failed"}']
        self.wipe()
        with redirect_stdout(io.StringIO()):
            res = admin.reapply_from_log(render_log + render_log, "owner",
                                         lambda f: admin.unlock_audit(f, source="reapply"))
        self.assertEqual([r["result"] for r in res], ["rebuilt"])      # deduped, idempotent
        self.settle()
        paid = json.loads((self.d / "paid.json").read_text())
        self.assertEqual(paid["source"], "admin:wechat:4200001234202610220000")
        self.assertEqual(len(self.runs), 2)

    def test_admin_unlock_rebuilds_with_url(self):
        self.wipe()
        with self.assertRaises(admin.AdminError) as cm, redirect_stdout(io.StringIO()):
            admin.unlock_audit(admin.validate(BODY), source="endpoint")
        self.assertEqual(cm.exception.http, 404)
        res, lines = self.unlock(dict(BODY, url=f"https://{DOMAIN}/products/x"))
        self.assertEqual(res["result"], "rebuilt")
        self.assertIn('"result": "rebuilt"', lines[-1])
        with self.assertRaises(admin.AdminError):
            admin.validate(dict(BODY, url="not a domain"))


if __name__ == "__main__":
    unittest.main()
