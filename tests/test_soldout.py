"""Sold-out throttle: per-day paid slots, Stripe-derived schedule, overflow orders held FIFO with honest ETAs,
recovery after a wiped disk, and the landing page's sold-out state (headless Chrome)."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from aiv import capacity, payments, server, slots

LINK = "plink_test_aiv"
ENV = {"AIV_PAID_REPORTS_PER_DAY": "1", "AIV_FREE_DAILY_CAP": "6", "AIV_STRIPE_PAYMENT_LINK_ID": LINK,
       "STRIPE_RESTRICTED_KEY": "rk_test_readonly"}


def wait_for(pred, n=150):
    for _ in range(n):
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.cap = capacity.Capacity()
        self.book = slots.Book()
        for p in (mock.patch("aiv.audit.DATA_DIR", self.data), mock.patch("aiv.server.DATA_DIR", self.data),
                  mock.patch("aiv.capacity.CAP", self.cap), mock.patch("aiv.slots.BOOK", self.book),
                  mock.patch("aiv.server.slots.BOOK", self.book), mock.patch.dict(os.environ, ENV)):
            p.start()
            self.addCleanup(p.stop)
        server._running.clear()
        self.addCleanup(server._running.clear)


def tomorrow():
    return capacity.next_day(capacity.quota_day())


class TestCapReached(Base):
    def test_one_slot_a_day_then_sold_out(self):
        st = self.cap.status()
        self.assertFalse(st["sold_out"])
        self.assertEqual(st["paid_slots_left_today"], 1)
        hold = threading.Event()
        self.cap.enqueue("a", lambda: hold.wait(5) and "done")
        self.cap.enqueue("b", lambda: "done")
        self.assertTrue(wait_for(lambda: self.cap.current == "a"))
        st = self.cap.status()
        self.assertTrue(st["sold_out"])
        self.assertEqual(st["paid_slots_left_today"], 0)
        self.assertEqual(self.cap.queued_day("b"), tomorrow())
        self.assertEqual(st["next_slot_cn"], capacity.cn_time(capacity.day_start(capacity.next_day(tomorrow()))
                                                              + capacity.DAY_START_DELAY))   # tomorrow's slot is b's
        hold.set()
        time.sleep(0.2)
        self.assertEqual(self.cap.queued_day("b"), tomorrow())      # held: never run before its day

    def test_held_order_runs_on_next_quota_day(self):
        done = []
        self.cap.enqueue("a", lambda: done.append("a") or "done")
        self.cap.enqueue("b", lambda: done.append("b") or "done")
        self.assertTrue(wait_for(lambda: done == ["a"]))
        time.sleep(0.1)
        self.assertEqual(done, ["a"])
        nxt = tomorrow()
        with mock.patch("aiv.capacity.quota_day", lambda now=None: nxt):
            with self.cap.lock:
                self.cap.cv.notify_all()
            self.assertTrue(wait_for(lambda: done == ["a", "b"]))

    def test_stripe_bookings_count_after_a_wipe(self):
        """Local counters are gone (fresh disk), but today's paid session in Stripe still takes today's slot."""
        self.book.ok = True
        self.book.sessions = [{"id": "cs_test_" + "a" * 20, "created": int(time.time()) - 60, "audit_id": "x", "domain": None}]
        self.assertTrue(self.cap.status()["sold_out"])
        self.assertEqual(self.cap.assign_day(), tomorrow())


class TestSchedule(Base):
    def test_deterministic_fifo_days_and_refunds_free_slots(self):
        now = int(time.time())
        def sess(i, refunded=False):
            return {"id": f"cs_live_{'%020d' % i}", "object": "checkout.session", "payment_link": LINK, "mode": "payment",
                    "status": "complete", "payment_status": "paid", "amount_total": 1900, "currency": "usd",
                    "created": now - 300 + i, "client_reference_id": f"20261022-10000{i}-abcdef",
                    "payment_intent": {"latest_charge": {"refunded": refunded, "amount_refunded": 1900 if refunded else 0}}}
        page = {"data": [sess(3), sess(2, refunded=True), sess(1), sess(4)], "has_more": False}
        r = mock.Mock(status_code=200)
        r.json.return_value = page
        with mock.patch("aiv.slots.requests.get", return_value=r) as g:
            self.assertTrue(self.book.refresh(force=True))
        params = dict(g.call_args.kwargs["params"])
        self.assertEqual(params["payment_link"], LINK)
        sched = self.book.schedule()
        days = [sched[f"cs_live_{'%020d' % i}"][0] for i in (1, 3, 4)]
        t = capacity.quota_day()
        self.assertEqual(days, [t, capacity.next_day(t), capacity.next_day(capacity.next_day(t))])
        self.assertNotIn(f"cs_live_{'%020d' % 2}", sched)                       # refunded: slot freed
        self.assertEqual(slots.Book.schedule(self.book), sched)                  # same input -> same schedule

    def test_stripe_down_keeps_local_counting(self):
        with mock.patch("aiv.slots.requests.get", side_effect=OSError("down")):
            self.assertFalse(self.book.refresh(force=True))
        self.assertEqual(self.cap.status()["stripe_schedule"], "unavailable")
        self.assertFalse(self.cap.status()["sold_out"])


class TestOverflowEta(Base):
    def test_overflow_order_queued_with_eta_and_refund_offer(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.cap.enqueue("20261022-100001-aaaaaa", lambda: hold.wait(5) and "done")
        self.assertTrue(wait_for(lambda: self.cap.current is not None))
        new = self.cap.eta(None)
        self.assertGreaterEqual(new["eta_ts"], capacity.next_reset())       # sold out: next quota day
        aid = "20261022-100002-bbbbbb"
        d = server.report_dir(aid)
        d.mkdir(parents=True)
        (d / "audit.json").write_text(json.dumps({"url": "https://shop.example/", "root": "https://shop.example/"}))
        with mock.patch("aiv.server._full_job", return_value="done"):
            self.assertTrue(server.ensure_paid(aid, None, "paid-redirect:cs_test_" + "b" * 20))
            self.assertEqual(json.loads((d / "paid.json").read_text())["day"], tomorrow())
            self.assertEqual(self.cap.queued_day(aid), tomorrow())
            e = self.cap.eta(aid)
            self.assertEqual(e["queue_ahead"], 1)
            self.assertAlmostEqual(e["eta_ts"], capacity.day_start(tomorrow()) + capacity.DAY_START_DELAY
                                   + capacity.REPORT_MINUTES * 60, delta=2)
            body = self._order_page(aid)
            self.assertIn("今日名额已售罄", body)
            self.assertIn("Today's slots are sold out", body)
            self.assertIn(e["eta_cn"], body)
            # a third order lands 2 quota days out -> more than 24 h -> the 7-day refund is offered
            aid3 = "20261022-100003-cccccc"
            d3 = server.report_dir(aid3)
            d3.mkdir(parents=True)
            (d3 / "audit.json").write_text(json.dumps({"url": "https://shop3.example/"}))
            server.ensure_paid(aid3, None, "paid-redirect:cs_test_" + "c" * 20)
            self.assertFalse(self.cap.eta(aid3)["within_24h"])
            body3 = self._order_page(aid3)
            self.assertIn("7 天内申请全额退款", body3)
            self.assertIn("full refund within 7 days", body3)

    def _order_page(self, aid):
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/order/{aid}") as r:
                return r.read().decode()
        finally:
            srv.shutdown()
            srv.server_close()


class TestRecoveryAfterWipe(Base):
    def test_sync_requeues_booked_orders_from_stripe_only(self):
        now = time.time()
        today = capacity.quota_day(now)
        reset = capacity.day_start(today)
        dom = "shop.example"
        mk = lambda i, created: {"id": f"cs_live_{'%020d' % i}", "created": int(created), "domain": dom,
                                 "audit_id": f"20261022-10000{i}-abcdef"}
        # yesterday's order (delivered or recoverable by its link), an overflow order booked for today whose slot
        # opened 10 minutes ago, and one booked for tomorrow
        self.book.sessions = [mk(1, reset - 3 * 3600), mk(2, reset - 2 * 3600), mk(3, reset - 3600)]
        self.book.ok = True
        self.book.refresh = lambda force=False: True
        sched = self.book.schedule()
        self.assertEqual([sched[f"cs_live_{'%020d' % i}"][0] for i in (1, 2, 3)],
                         [capacity.quota_day(reset - 3 * 3600), today, capacity.next_day(today)])
        clock = reset + capacity.DAY_START_DELAY + 600
        with mock.patch("aiv.server._full_job", return_value="done"), \
                mock.patch("aiv.server.is_public_host", return_value=True), \
                mock.patch("aiv.capacity.Capacity._run", lambda self: None):        # inspect the queue, don't run
            n = server.sync_from_stripe(now=clock)
        self.assertEqual(n, 2)
        q = {it[0]: it[2] for it in self.cap.queue}
        self.assertEqual(q, {"20261022-100002-abcdef": today, "20261022-100003-abcdef": capacity.next_day(today)})
        order = json.loads((server.report_dir("20261022-100002-abcdef") / "order.json").read_text())
        self.assertEqual(order["url"], "https://shop.example/")
        # 2 hours after the slot opened, a later wake-up doesn't regenerate today's (already delivered) report
        self.cap.queue.clear()
        server._running.clear()                       # a new process after the wipe
        shutil.rmtree(self.data / "reports")
        with mock.patch("aiv.capacity.Capacity._run", lambda self: None), \
                mock.patch("aiv.server.is_public_host", return_value=True):
            server.sync_from_stripe(now=reset + capacity.DAY_START_DELAY + 2 * 3600)
        self.assertEqual([it[0] for it in self.cap.queue], ["20261022-100003-abcdef"])


@unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "needs headless Chrome")
class TestLandingSoldOut(Base):
    def _dom(self):
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        with tempfile.TemporaryDirectory() as prof:
            out = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", f"--user-data-dir={prof}",
                                  "--virtual-time-budget=5000", "--dump-dom", f"http://127.0.0.1:{srv.server_address[1]}/"],
                                 capture_output=True, text=True, timeout=60)
        return out.stdout

    def test_sold_out_hides_buy_button_and_shows_message(self):
        hold = threading.Event()
        self.addCleanup(hold.set)
        self.cap.enqueue("a", lambda: hold.wait(10) and "done")
        self.assertTrue(wait_for(lambda: self.cap.current == "a"))
        html = self._dom()
        self.assertIn('id="soldOut" style="display: block;"', html)
        self.assertIn("今日名额已售罄，明天开放下一个名额", html)
        self.assertIn("Today's slots are sold out, next slot opens tomorrow", html)
        self.assertIn("下一个名额：北京时间", html)
        self.assertRegex(html, r'id="buyBtn"[^>]*style="display: none;"')

    def test_open_slots_keep_buy_button(self):
        html = self._dom()
        self.assertIn('id="soldOut" style="display:none"', html)
        self.assertNotRegex(html, r'id="buyBtn"[^>]*display: none')


if __name__ == "__main__":
    unittest.main()
