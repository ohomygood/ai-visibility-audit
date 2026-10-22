"""Manual cashier unlock: /admin/unlock endpoint + admin-unlock CLI."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from aiv import admin, cli, payments, server

AID = "20261022-121500-abc123"
TOKEN = "a" * 16 + "0123456789abcdef0123456789abcdef"   # 48 chars
BODY = {"audit_id": AID, "by": "cashier-li", "reason": "WeChat Pay ¥129 confirmed", "channel": "wechat",
        "amount": 129, "currency": "CNY", "ref": "4200001234202610220000"}


def fake_full(runs, url, out_dir):
    """Stand-in for the paid full audit: records the call and writes the report like the real one."""
    runs.append(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.zh.html").write_text("full report")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.d = self.data / "reports" / AID
        self.d.mkdir(parents=True)
        (self.d / "audit.json").write_text(json.dumps({"url": "https://shop.example"}))
        for p in (mock.patch("aiv.server.DATA_DIR", self.data), mock.patch("aiv.audit.DATA_DIR", self.data)):
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
        admin.reset_lockout()
        self.addCleanup(admin.reset_lockout)

    def log(self):
        p = self.data / "admin" / "unlocks.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class TestEndpoint(Base):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def post(self, body, token=TOKEN):
        h = {"Content-Type": "application/json"}
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + "/admin/unlock", data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            r = urllib.request.urlopen(req)
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_disabled_without_token_or_short_token(self):
        for env in ({"ADMIN_TOKEN": ""}, {"ADMIN_TOKEN": "short-token-1234"}):
            with mock.patch.dict(os.environ, env):
                code, _ = self.post(BODY, token=env["ADMIN_TOKEN"])
                self.assertEqual(code, 404)
        self.assertFalse((self.d / "paid.json").exists())

    def test_unlock_idempotent_and_logged(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}):
            code, j = self.post(BODY)
            self.assertEqual((code, j["result"]), (200, "unlocked"))
            self.assertTrue(j["order_url"].startswith(f"/order/{AID}?u="))   # server-signed, survives a wipe
            paid = json.loads((self.d / "paid.json").read_text())
            self.assertEqual(paid["source"], "admin:wechat:4200001234202610220000")
            self.assertEqual(paid["manual"]["amount"], 129.0)
            code, j = self.post(BODY)
            self.assertEqual((code, j["result"]), (200, "already_unlocked"))
        time.sleep(0.2)
        self.assertEqual(len(self.runs), 1)                 # same report trigger as /paid, fired once
        log = self.log()
        self.assertEqual([r["result"] for r in log], ["unlocked", "already_unlocked"])
        for r in log:
            for k in ("ts", "audit_id", "by", "reason", "channel", "amount", "currency", "ref", "source"):
                self.assertIn(k, r)
            self.assertEqual(r["source"], "endpoint")
        raw = (self.data / "admin").glob("*")
        self.assertFalse(any(TOKEN in p.read_text() for p in raw))   # token never logged

    def test_bad_token_lockout(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}):
            self.assertEqual(self.post(BODY, token=None)[0], 401)
            for _ in range(4):
                self.assertEqual(self.post(BODY, token="b" * 48)[0], 401)
            code, _ = self.post(BODY)                         # locked: even the right token is refused
            self.assertEqual(code, 429)
        self.assertFalse((self.d / "paid.json").exists())
        self.assertEqual(self.log(), [])
        sec = (self.data / "admin" / "security.jsonl").read_text()
        self.assertNotIn("b" * 48, sec)
        self.assertNotIn(TOKEN, sec)

    def test_global_lockout(self):
        now = time.time()
        for i in range(admin.FAIL_GLOBAL):
            with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}), self.assertRaises(admin.AdminError):
                admin.check_auth("Bearer wrong", f"10.0.0.{i}", now)
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}), self.assertRaises(admin.AdminError) as cm:
            admin.check_auth(f"Bearer {TOKEN}", "10.9.9.9", now)
        self.assertEqual(cm.exception.http, 429)
        # window expires
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}):
            admin.check_auth(f"Bearer {TOKEN}", "10.9.9.9", now + admin.FAIL_WINDOW + 1)

    def test_validation(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}):
            for patch in ({"by": ""}, {"reason": ""}, {"audit_id": "../x"}, {"channel": "cash?"},
                          {"amount": "lots"}, {"currency": "XYZ"}, {"ref": ""}, {"reason": "a\nb"}):
                code, _ = self.post({**BODY, **patch})
                self.assertEqual(code, 400, patch)
            code, _ = self.post({**BODY, "audit_id": "20200101-000000-000000"})
            self.assertEqual(code, 404)
        self.assertFalse((self.d / "paid.json").exists())
        self.assertEqual([r["result"] for r in self.log()], ["not_found"])

    def test_admin_and_paid_share_state(self):
        """Admin unlock first, then a Stripe /paid for the same audit: no second report run."""
        sess = {"id": "cs_test_a1B2c3D4e5F6g7H8i9J0", "object": "checkout.session", "mode": "payment",
                "status": "complete", "payment_status": "paid", "payment_link": "plink_x", "amount_total": 1900,
                "currency": "usd", "client_reference_id": AID}

        class R:
            status_code = 200
            def json(self):
                return sess
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN, "STRIPE_RESTRICTED_KEY": "rk_live_checkout_charges_balance_read",
                                          "AIV_STRIPE_PAYMENT_LINK_ID": "plink_x"}), \
             mock.patch("aiv.payments.requests.get", return_value=R()):
            self.assertEqual(self.post(BODY)[0], 200)
            urllib.request.urlopen(self.base + f"/paid?session_id={sess['id']}")   # follows to /order
        time.sleep(0.2)
        self.assertEqual(len(self.runs), 1)


class TestStdoutAudit(Base):
    def test_admin_audit_lines_on_stdout_without_secrets(self):
        env = {"ADMIN_TOKEN": TOKEN, "STRIPE_RESTRICTED_KEY": "rk_live_secretvalue_should_not_leak"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), redirect_stdout(out):
            admin.unlock_audit(admin.validate(BODY), source="endpoint")
            admin.unlock_audit(admin.validate({**BODY, "reason": "contains " + TOKEN}), source="cli")
            with self.assertRaises(admin.AdminError):
                admin.check_auth("Bearer " + "z" * 40, "9.9.9.9")
        lines = [l for l in out.getvalue().splitlines() if l.startswith("ADMIN_AUDIT ")]
        self.assertEqual(len(lines), 3)
        recs = [json.loads(l[len("ADMIN_AUDIT "):]) for l in lines]
        self.assertEqual([r.get("result") or r.get("event") for r in recs], ["unlocked", "already_unlocked", "admin_auth_failed"])
        self.assertEqual(recs[0]["ref"], BODY["ref"])
        text = out.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("rk_live_secretvalue_should_not_leak", text)
        self.assertNotIn("z" * 40, text)

    def test_export_script(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("exp", Path(__file__).resolve().parents[1] / "scripts" / "export_admin_log.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with redirect_stdout(io.StringIO()):
            admin.unlock_audit(admin.validate(BODY), source="cli")
        m.FILES = {"admin_unlocks": self.data / "admin" / "unlocks.jsonl"}
        files = m.export(Path(self.tmp.name) / "exp", "20261022")
        self.assertEqual([f.name for f in files], ["admin_unlocks_20261022.jsonl"])


class TestRestrictedKeyScope(unittest.TestCase):
    def test_broader_readonly_rk_works_sk_refused(self):
        class R:
            status_code = 200
            def json(self):
                return {}
        with mock.patch("aiv.payments.requests.get", return_value=R()) as g:
            with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": "rk_live_with_charges_and_balance_read"}):
                payments.fetch_session("cs_live_a1B2c3D4e5F6g7H8")
            self.assertEqual(g.call_count, 1)
            self.assertTrue(g.call_args.args[0].startswith("https://api.stripe.com/v1/checkout/sessions/"))
            for bad in ("sk_live_full", "pk_live_pub", "whatever"):
                with mock.patch.dict(os.environ, {"STRIPE_RESTRICTED_KEY": bad}), self.assertRaises(payments.PaymentError):
                    payments.fetch_session("cs_live_a1B2c3D4e5F6g7H8")
            self.assertEqual(g.call_count, 1)


class TestCLI(Base):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["admin-unlock", *args])
        lines = [l for l in out.getvalue().splitlines() if not l.startswith("ADMIN_AUDIT ")]
        return code, "\n".join(lines), err.getvalue()

    def test_cli_unlock_runs_report_and_logs(self):
        args = [AID, "--by", "the owner", "--reason", "WeChat ¥129", "--channel", "wechat", "--amount", "129", "--ref", "wx-note-001"]
        code, out, _ = self.run_cli(*args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["result"], "unlocked")
        self.assertEqual(len(self.runs), 1)                   # ran synchronously in the CLI process
        code, out, _ = self.run_cli(*args)
        self.assertEqual(json.loads(out)["result"], "already_unlocked")
        log = self.log()
        self.assertEqual([(r["source"], r["result"], r["currency"]) for r in log],
                         [("cli", "unlocked", "CNY"), ("cli", "already_unlocked", "CNY")])

    def test_cli_requires_ref_for_wechat(self):
        code, _, err = self.run_cli(AID, "--by", "the owner", "--reason", "x", "--channel", "wechat")
        self.assertEqual(code, 1)
        self.assertIn("ref", err)
        self.assertFalse((self.d / "paid.json").exists())

    def test_cli_remote_needs_token(self):
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": ""}):
            code, _, err = self.run_cli(AID, "--by", "y", "--reason", "x", "--ref", "r1", "--remote", "http://127.0.0.1:9")
        self.assertEqual(code, 2)

    def test_cli_remote_sends_bearer(self):
        class R:
            status_code = 200
            text = '{"result": "unlocked"}'
        with mock.patch.dict(os.environ, {"ADMIN_TOKEN": TOKEN}), mock.patch("requests.post", return_value=R()) as p:
            code, out, _ = self.run_cli(AID, "--by", "y", "--reason", "x", "--ref", "r1", "--remote", "https://api.example/")
        self.assertEqual(code, 0)
        self.assertEqual(p.call_args.args[0], "https://api.example/admin/unlock")
        self.assertEqual(p.call_args.kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, out)


class TestStripeOnlyCopy(unittest.TestCase):
    """Stripe only: no WeChat price, QR or screenshot instructions on any buyer-facing page."""
    BAD = ("微信", "¥129", "¥99", "¥29", "wechat", "收款码", "qr", "付款截图", "人民币", "无理由", "不问原因", "no questions asked",
           "2 个 ai 引擎", "2 ai engines", "可用本地货币付款", "早鸟", "coupon", "优惠码")
    CONTACT = "go_with_prob@mail.grokbot.com"

    def assert_clean(self, text, where):
        low = text.lower()
        for b in self.BAD:
            self.assertNotIn(b.lower(), low, f"{b!r} in {where}")

    def test_landing_and_config(self):
        root = Path(__file__).resolve().parents[1]
        for f in ("index.html", "config.js", "privacy.html", "terms.html", "i18n.js"):
            self.assert_clean((root / "landing" / f).read_text(), f)
        idx = (root / "landing" / "index.html").read_text()
        self.assertIn("按美元（USD）收费", idx)
        self.assertIn("checkout may show the price in your local currency", idx)
        for f in ("index.html", "privacy.html", "terms.html"):
            t = (root / "landing" / f).read_text()
            self.assertIn(f"mailto:{self.CONTACT}", t, f)
            self.assertNotIn('data-text="CONTACT_EMAIL"', t, f)
        for f in ("index.html", "terms.html"):     # refund policy: 7 days, not delivered in 24h or not useful, once per customer
            t = (root / "landing" / f).read_text()
            for must in ("24 小时内交付", "没有帮助", "每位客户限退款一次", "within 24 hours", "not useful", "One refund per customer"):
                self.assertIn(must, t, f"{must!r} missing in {f}")
        # engines: only what production calls (Gemini), the others as coming soon
        self.assertIn("5 个买家问题 × Gemini", idx)
        self.assertIn("ChatGPT 和 Perplexity 即将支持", idx)
        self.assertIn("coming soon", idx)

    def test_report_paywall(self):
        from aiv.report import write_reports
        import tempfile
        res = json.loads((Path(__file__).resolve().parents[1] / "samples" / "gemini-real" / "quick" / "allbirds.com" / "audit.json").read_text())
        with tempfile.TemporaryDirectory() as d:
            write_reports(res, Path(d))
            for f in ("report.zh.html", "report.en.html"):
                html = (Path(d) / f).read_text()
                self.assert_clean(html.replace(res["audit_id"], ""), f)
            zh = (Path(d) / "report.zh.html").read_text()
            self.assertIn("$19 美元", zh)
            self.assertIn("ChatGPT 和 Perplexity 即将支持", zh)
            self.assertIn(self.CONTACT, zh)

    def test_order_pages(self):
        from aiv import server
        for args in (("Locked", "<p>x</p>"),):
            self.assert_clean(server.page(*args).decode(), "order page")
        self.assert_clean(json.dumps(server.PAY_ERRORS, ensure_ascii=False), "payment messages")
        self.assertIn(self.CONTACT, server.CONTACT_HTML)

    def test_static_build_rejects_qr(self):
        import shutil
        import subprocess
        import tempfile
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            site = Path(d) / "site"
            shutil.copytree(root / "landing", site, ignore=shutil.ignore_patterns("*.jpg", "*.png"))
            chk = [sys.executable, str(root / "scripts" / "check_release.py"), str(site)]
            self.assertEqual(subprocess.run(chk, capture_output=True).returncode, 0)
            (site / "wechat_pay_qr.jpg").write_bytes(b"\xff\xd8fake")
            self.assertEqual(subprocess.run(chk, capture_output=True).returncode, 1)
            (site / "wechat_pay_qr.jpg").unlink()
            (site / "pay.html").write_text("<p>微信支付 ¥129</p>")
            self.assertEqual(subprocess.run(chk, capture_output=True).returncode, 1)



if __name__ == "__main__":
    unittest.main()


class TestHealth(Base):
    def test_health_production_flags(self):
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with mock.patch.dict(os.environ, {"AIV_ENV": "production", "AIV_DEV_UNLOCK": "1", "GEMINI_API_KEY": "x" * 39,
                                              "STRIPE_RESTRICTED_KEY": "", "ADMIN_TOKEN": TOKEN}):
                j = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/healthz").read())
            self.assertEqual(j, {"ok": True, "production": True, "engines": "gemini", "payments_configured": False,
                                 "admin_enabled": True, "order_links": True, "dev_unlock": False})
            self.assertNotIn("x" * 39, json.dumps(j))
        finally:
            srv.shutdown()
            srv.server_close()
