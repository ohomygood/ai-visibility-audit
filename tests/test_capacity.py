"""Free-check daily cap, paid-first queue with honest ETAs, quota deferral, and the production no-mock guard."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aiv import capacity, server
from aiv.llm import ProductionStubError


class CapBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch("aiv.audit.DATA_DIR", Path(self.tmp.name))
        p.start()
        self.addCleanup(p.stop)
        self.cap = capacity.Capacity()


class TestFreeCap(CapBase):
    def test_cap_then_honest_message(self):
        with mock.patch.dict(os.environ, {"AIV_FREE_DAILY_CAP": "2"}):
            self.assertEqual(self.cap.take_free(), (True, ""))
            self.assertEqual(self.cap.take_free(), (True, ""))
            ok, why = self.cap.take_free()
        self.assertEqual((ok, why), (False, "cap"))
        msg = capacity.free_blocked_message(why)
        self.assertIn("今天的免费检测名额已用完，请明天再试", msg["message_zh"])
        self.assertIn("Today's free checks are used up", msg["message_en"])

    def test_counter_persists_and_rolls_over(self):
        with mock.patch.dict(os.environ, {"AIV_FREE_DAILY_CAP": "1"}):
            self.assertTrue(self.cap.take_free()[0])
            self.assertFalse(capacity.Capacity().take_free()[0])      # same disk = survives a process restart
            with mock.patch("aiv.capacity.quota_day", return_value="2999-01-01"):
                self.assertTrue(capacity.Capacity().take_free()[0])  # new quota day

    def test_paid_backlog_pauses_free_checks(self):
        with mock.patch.dict(os.environ, {"AIV_PAID_REPORTS_PER_DAY": "1"}):
            self.cap.queue = [("a", None), ("b", None)]
            self.assertEqual(self.cap.take_free(), (False, "paid_first"))

    def test_gemini_out_of_quota(self):
        with mock.patch("aiv.capacity.gemini_quota_left", return_value=False):
            self.assertEqual(self.cap.take_free(), (False, "quota"))


class TestEta(CapBase):
    def test_eta_within_and_beyond_capacity(self):
        with mock.patch.dict(os.environ, {"AIV_PAID_REPORTS_PER_DAY": "2"}):
            self.assertTrue(self.cap.eta(None)["within_24h"])
            self.cap.queue = [(f"x{i}", None) for i in range(6)]   # 6 waiting at 2/day -> next order ~3+ days out
            e = self.cap.eta(None)
            self.assertFalse(e["within_24h"])
            self.assertGreater(e["eta_hours"], 48)
            self.assertEqual(self.cap.eta("x0")["queue_ahead"], 0)

    def test_status_shape(self):
        st = self.cap.status()
        for k in ("free_cap", "free_left_today", "paid_queue", "new_order", "quota_day_resets_cn"):
            self.assertIn(k, st)


class TestDeferAndGuard(unittest.TestCase):
    def test_quota_failure_detected(self):
        rows = [{"error": "RuntimeError: all Gemini models hit their free-tier daily quota"}] * 30 + [{"error": None}] * 45
        self.assertTrue(server._quota_failed({"visibility": {"rows": rows}}))
        self.assertFalse(server._quota_failed({"visibility": {"rows": [{"error": None}] * 75}}))
        self.assertFalse(server._quota_failed(None))

    def test_deferred_job_is_not_published(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            def fake(url, mode, out_dir):
                out_dir.mkdir(parents=True)
                (out_dir / "report.zh.html").write_text("partial")
                return {"visibility": {"rows": [{"error": "daily quota"}] * 10}}
            with mock.patch("aiv.server.run_audit", side_effect=fake):
                self.assertEqual(server._full_job("x", "https://s.example/", d), "defer")
            self.assertFalse((d / "full").exists())
            self.assertFalse((d / "full_error.txt").exists())

    def test_production_refuses_mock(self):
        for env in ({"AIV_ENV": "production", "AIV_ENGINES": "stub"},
                    {"AIV_ENV": "production", "AIV_ENGINES": "", "AIV_ENGINE_MODE": "", "GEMINI_API_KEY": ""},
                    {"AIV_ENV": "production", "AIV_DEV_UNLOCK": "1", "GEMINI_API_KEY": "x"}):
            with mock.patch.dict(os.environ, env):
                with self.assertRaises((SystemExit, ProductionStubError, RuntimeError)):
                    server.production_guard()

    def test_production_accepts_gemini(self):
        with mock.patch.dict(os.environ, {"AIV_ENV": "production", "AIV_ENGINES": "", "AIV_ENGINE_MODE": "gemini-only",
                                          "GEMINI_API_KEY": "test-not-a-key", "AIV_DEV_UNLOCK": "0"}):
            server.production_guard()

    def test_simulated_output_refused_in_production(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, {"AIV_ENV": "production"}):
            d = Path(t)
            def fake(url, mode, out_dir):
                out_dir.mkdir(parents=True)
                return {"simulated": True, "visibility": {"rows": []}}
            with mock.patch("aiv.server.run_audit", side_effect=fake):
                server._full_job("x", "https://s.example/", d)
            self.assertIn("simulated", (d / "full_error.txt").read_text())
            self.assertFalse((d / "full").exists())


class TestQueueRuns(CapBase):
    def test_worker_runs_in_order(self):
        done = []
        with mock.patch.dict(os.environ, {"AIV_PAID_REPORTS_PER_DAY": "2"}):
            for a in ("a", "b"):
                self.cap.enqueue(a, lambda a=a: done.append(a) or "done")
            for _ in range(100):
                if len(done) == 2:
                    break
                time.sleep(0.02)
        self.assertEqual(done, ["a", "b"])


class TestEndpoints(CapBase):
    def test_check_at_cap_and_capacity_endpoint(self):
        import json
        import threading
        import urllib.error
        import urllib.request
        cap = capacity.Capacity()
        for p in (mock.patch("aiv.capacity.CAP", cap), mock.patch("aiv.server.is_public_host", return_value=True),
                  mock.patch.dict(os.environ, {"AIV_FREE_DAILY_CAP": "0"})):
            p.start()
            self.addCleanup(p.stop)
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with mock.patch("aiv.server.run_audit") as ra:
            req = urllib.request.Request(base + "/api/check", data=json.dumps({"url": "https://shop.example"}).encode(),
                                         headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            body = json.loads(cm.exception.read())
            ra.assert_not_called()                       # no Gemini call when the cap is hit
        self.assertEqual(cm.exception.code, 429)
        self.assertIn("请明天再试", body["message_zh"])
        st = json.loads(urllib.request.urlopen(base + "/api/capacity").read())
        self.assertEqual(st["free_left_today"], 0)
        self.assertTrue(st["new_order"]["within_24h"])
