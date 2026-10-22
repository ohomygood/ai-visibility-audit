"""Daily Gemini budget: a global cap on free quick checks, and a single-worker, day-slotted queue for paid reports.

Capacity math (full derivation in docs/capacity.md):
  * engine chain (AIV_GEMINI_MODELS unset -> DEFAULT_MODELS): gemini-3.5-flash, gemini-3.6-flash,
    gemini-3.5-flash-lite, gemini-flash-lite-latest; a per-day 429 moves to the next model.
  * free-tier requests/day: 3.5-flash 20, 3.6-flash 20 (quota value in the 429 body); 3.5-flash-lite: no 429 at
    100+ requests in one day (limit not disclosed) -> counted as 100; flash-lite-latest is an alias of the
    flash-lite line -> counted as 0. Planning budget 140 calls/day, minus ~10% for retries/probes -> ~126.
  * one quick check = 5 calls (5 questions x 1 sample); one full report = 80 (25 questions x 3 samples + 5 drafts).
  * 1 paid report (80) + 6 free checks (30) = 110 <= 126. A 2nd paid report (160 total) does not fit.
  => AIV_PAID_REPORTS_PER_DAY=1, AIV_FREE_DAILY_CAP=6.

Quota days follow Google's reset: midnight America/Los_Angeles (15:00 or 16:00 Beijing time).

Paid orders: each order gets a quota day (first day with a free slot, oldest payment first); the worker never runs
an order before its day and never more than AIV_PAID_REPORTS_PER_DAY a day. When today's slots are taken the site
shows "sold out"; an order paid anyway (direct Stripe link) is queued for its day and its /order page shows the ETA.
Surviving restarts/wipes: aiv/slots.py rebuilds the day schedule from Stripe's Checkout Sessions; at startup and
every 10 minutes the server re-queues booked orders whose day has come (see server.sync_from_stripe), and the
buyer's order link (/order/<id>?s=<session>) re-verifies and re-queues the order too.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
CN = ZoneInfo("Asia/Shanghai")
CALLS_PER_QUICK = 5
CALLS_PER_FULL = 80
REPORT_MINUTES = 12          # measured 8.6 min for a full report at 10 RPM; rounded up
DAY_START_DELAY = 120        # seconds after the quota reset before a day's first slot is promised


def free_cap() -> int:
    return max(0, int(os.environ.get("AIV_FREE_DAILY_CAP", "6")))


def paid_per_day() -> int:
    return max(1, int(os.environ.get("AIV_PAID_REPORTS_PER_DAY", "1")))


def quota_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, PT).strftime("%Y-%m-%d")


def day_start(day: str) -> float:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, tzinfo=PT).timestamp()


def next_day(day: str) -> str:
    y, m, d = (int(x) for x in day.split("-"))
    n = datetime(y, m, d) + timedelta(days=1)
    return n.strftime("%Y-%m-%d")


def next_reset(now: float | None = None) -> float:
    return day_start(next_day(quota_day(now)))


def cn_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, CN).strftime("%m-%d %H:%M")


def gemini_quota_left() -> bool:
    """Only meaningful when production runs on Gemini; dev/stub runs always have quota."""
    from .llm import is_production
    if not is_production():
        return True
    from .llm.providers import GeminiProvider
    return GeminiProvider.quota_left()


def _stripe_booked(day: str) -> int:
    from .slots import BOOK
    return BOOK.booked(day) if BOOK.ok else 0


class Capacity:
    def __init__(self):
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.queue: list[tuple] = []          # (audit_id, job, day) waiting, FIFO
        self.current: str | None = None
        self.current_started = 0.0
        self.worker: threading.Thread | None = None

    # ---- persisted counters (data/capacity.json) ----
    def _path(self) -> Path:
        from . import audit
        return audit.DATA_DIR / "capacity.json"

    def _load(self) -> dict:
        try:
            st = json.loads(self._path().read_text())
        except (OSError, ValueError):
            st = {}
        if st.get("day") != quota_day():
            st = {"day": quota_day(), "free_used": 0, "paid_started": 0}
        return st

    def _save(self, st: dict) -> None:
        p = self._path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(st))
        except OSError:
            pass

    @staticmethod
    def _day(item) -> str:
        return item[2] if len(item) > 2 and item[2] else quota_day()

    # ---- slots (call with self.lock held) ----
    def _booked(self, day: str, st: dict | None = None) -> int:
        today = quota_day()
        local = sum(1 for it in self.queue if self._day(it) == day)
        if day == today:
            local += (st or self._load())["paid_started"]
        return max(local, _stripe_booked(day))

    def _assign_day(self, st: dict | None = None) -> str:
        d, per = quota_day(), paid_per_day()
        for _ in range(400):
            if self._booked(d, st) < per:
                return d
            d = next_day(d)
        return d

    def assign_day(self) -> str:
        with self.lock:
            return self._assign_day()

    def sold_out(self) -> bool:
        with self.lock:
            return self._booked(quota_day()) >= paid_per_day()

    # ---- free checks ----
    def take_free(self) -> tuple[bool, str]:
        """Reserve one free quick check. Returns (ok, reason) with reason in cap|paid_first|quota."""
        with self.lock:
            st = self._load()
            if st["free_used"] >= free_cap():
                return False, "cap"
            today = quota_day()
            waiting = sum(1 for it in self.queue if self._day(it) <= today) + (1 if self.current else 0)
            if waiting > max(0, paid_per_day() - st["paid_started"]):
                return False, "paid_first"
            if not gemini_quota_left():
                return False, "quota"
            st["free_used"] += 1
            self._save(st)
            return True, ""

    # ---- paid queue ----
    def enqueue(self, audit_id: str, job, day: str | None = None) -> bool:
        """job() -> "done" | "defer". day: quota day the order may run (default: first day with a free slot).
        Returns False if the audit is already queued or running."""
        with self.lock:
            if audit_id == self.current or any(it[0] == audit_id for it in self.queue):
                return False
            self.queue.append((audit_id, job, day or self._assign_day()))
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name="paid-queue", daemon=True)
                self.worker.start()
            self.cv.notify_all()
            return True

    def busy(self, audit_id: str) -> bool:
        with self.lock:
            return audit_id == self.current or any(it[0] == audit_id for it in self.queue)

    def queued_day(self, audit_id: str) -> str | None:
        with self.lock:
            return next((self._day(it) for it in self.queue if it[0] == audit_id), None)

    def _next_ready(self):
        """Index of the first queued order whose day has come and for which today has a slot, else None."""
        today = quota_day()
        if self._load()["paid_started"] >= paid_per_day():
            return None
        for i, it in enumerate(self.queue):
            if self._day(it) <= today:
                return i
        return None

    def _run(self):
        while True:
            with self.lock:
                while True:
                    i = self._next_ready()
                    if i is not None:
                        break
                    self.cv.wait(timeout=max(5.0, min(600.0, next_reset() + 5 - time.time())))
                aid, job, day = self.queue.pop(i)
                self.current, self.current_started = aid, time.time()
            if not gemini_quota_left():
                self._wait_for_quota()
            with self.lock:
                st = self._load()
                st["paid_started"] += 1
                self._save(st)
            try:
                out = job()
            except Exception:  # noqa: BLE001  job records its own errors
                out = "done"
            with self.lock:
                self.current = None
                if out == "defer":                 # ran out of quota midway: first in line after the reset
                    self.queue.insert(0, (aid, job, next_day(quota_day())))
            if out == "defer":
                self._wait_for_quota(force=True)

    def _wait_for_quota(self, force: bool = False):
        """Sleep until Gemini quota is back (re-checked every 10 minutes, at the latest just after the PT reset)."""
        first = True
        while force and first or not gemini_quota_left():
            first = False
            time.sleep(max(1.0, min(600.0, next_reset() + 120 - time.time())))

    # ---- honest ETAs ----
    def _timeline(self, extra: str | None = None) -> dict:
        """Simulate the worker: audit_id -> ready timestamp. extra adds a virtual new order at the end."""
        now = time.time()
        per = paid_per_day()
        st = self._load()
        today = quota_day(now)
        counts = {today: st["paid_started"]}
        t = now
        out = {}
        if self.current:
            t = max(now, self.current_started + REPORT_MINUTES * 60)
            out[self.current] = t
        items = [(it[0], self._day(it)) for it in self.queue]
        if extra:
            items.append((extra, self._assign_day(st)))
        quota_ok = gemini_quota_left()
        for aid, day in items:
            d = max(day, quota_day(t))
            while counts.get(d, 0) >= per or (d == today and not quota_ok):
                d = next_day(d)
            start = t if d == quota_day(t) else max(t, day_start(d) + DAY_START_DELAY)
            counts[d] = counts.get(d, 0) + 1
            t = start + REPORT_MINUTES * 60
            out[aid] = t
        return out

    def eta(self, audit_id: str | None = None) -> dict:
        """Honest delivery estimate for an order already queued (audit_id) or for a new order (None)."""
        key = audit_id or "__new__"
        with self.lock:
            ids = ([self.current] if self.current else []) + [it[0] for it in self.queue]
            ahead = ids.index(audit_id) if audit_id in ids else len(ids)
            tl = self._timeline(None if audit_id in ids else key)
        now = time.time()
        ready = tl.get(key) or tl.get(audit_id) or now + REPORT_MINUTES * 60
        hours = max(0.0, (ready - now) / 3600)
        return {"queue_ahead": ahead, "eta_ts": int(ready), "eta_hours": round(hours, 1), "within_24h": hours <= 24,
                "eta_cn": cn_time(ready)}

    def status(self) -> dict:
        from .slots import BOOK
        with self.lock:
            st = self._load()
            waiting = len(self.queue) + (1 if self.current else 0)
            today = quota_day()
            booked = self._booked(today, st)
            nxt = self._assign_day(st)
        per = paid_per_day()
        cap = free_cap()
        sold_out = booked >= per
        return {"paid_reports_per_day": per, "paid_slots_left_today": max(0, per - booked), "sold_out": sold_out,
                "next_slot_cn": cn_time(time.time() if nxt == today else day_start(nxt) + DAY_START_DELAY),
                "quota_day_resets_cn": cn_time(next_reset()), "paid_queue": waiting,
                "free_cap": cap, "free_left_today": max(0, cap - st["free_used"]),
                "stripe_schedule": "ok" if BOOK.ok else "unavailable",
                "new_order": self.eta(None)}


CAP = Capacity()

FREE_BLOCKED = {
    "cap": ("今天的免费检测名额已用完，请明天再试（北京时间 {reset} 后重置）。付费报告不受影响。",
            "Today's free checks are used up. Please try again tomorrow (resets {reset} Beijing time). Paid reports are not affected."),
    "paid_first": ("今天的 AI 调用额度优先留给付费订单，免费检测暂停，请明天再试（北京时间 {reset} 后重置）。",
                   "Today's AI quota is reserved for paid orders, so free checks are paused. Please try again tomorrow (resets {reset} Beijing time)."),
    "quota": ("今天的免费检测名额已用完，请明天再试（北京时间 {reset} 后重置）。",
              "Today's free checks are used up. Please try again tomorrow (resets {reset} Beijing time)."),
}


def free_blocked_message(reason: str) -> dict:
    zh, en = FREE_BLOCKED.get(reason, FREE_BLOCKED["cap"])
    r = cn_time(next_reset())
    return {"error": en.format(reset=r), "code": "free_daily_cap", "reason": reason,
            "message_zh": zh.format(reset=r), "message_en": en.format(reset=r)}
