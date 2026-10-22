"""Daily Gemini budget: a global cap on free quick checks, and a single-worker queue for paid reports.

Measured on 2026-10-22 (samples/gemini-real/README.md):
  * one free quick check   = 5 Gemini calls (5 questions x 1 sample)
  * one paid full report   = 80 Gemini calls (25 questions x 3 samples + 5 content drafts)
  * free-tier daily quota per model: gemini-3.5-flash and gemini-3.6-flash 20 requests/day each (quota value 20);
    gemini-3.5-flash-lite: 100 requests in one day with no 429 (Google does not disclose the limit).
  => planning budget ~240 calls/day: 40 on the flash models, ~200 assumed on flash-lite (raise it once observed).

Paid orders come first. AIV_PAID_REPORTS_PER_DAY (default 2) reserves 2 x 80 = 160 calls; the free cap gets the rest:
AIV_FREE_DAILY_CAP (default 15) x 5 = 75 calls. Free checks also stop while paid orders are waiting for more capacity
than is left today, or when every Gemini model is out of quota.

Quota days follow Google's reset: midnight America/Los_Angeles (15:00 or 16:00 Beijing time).

Restarts: the queue is rebuilt at startup from data/reports/*/paid.json, and after a full disk wipe the buyer's order link
(/order/<id>?s=<session>) re-verifies the payment and re-queues the report. The free-check counter lives on the same
disk, so a wipe resets it; Gemini's own daily quota is then the backstop (the free check says so honestly).
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


def free_cap() -> int:
    return max(0, int(os.environ.get("AIV_FREE_DAILY_CAP", "15")))


def paid_per_day() -> int:
    return max(1, int(os.environ.get("AIV_PAID_REPORTS_PER_DAY", "2")))


def quota_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now or time.time(), PT).strftime("%Y-%m-%d")


def next_reset(now: float | None = None) -> float:
    d = datetime.fromtimestamp(now or time.time(), PT)
    return (datetime(d.year, d.month, d.day, tzinfo=PT) + timedelta(days=1)).timestamp()


def cn_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, CN).strftime("%m-%d %H:%M")


def gemini_quota_left() -> bool:
    """Only meaningful when production runs on Gemini; dev/stub runs always have quota."""
    from .llm import is_production
    if not is_production():
        return True
    from .llm.providers import GeminiProvider
    return GeminiProvider.quota_left()


class Capacity:
    def __init__(self):
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.queue: list[tuple[str, object]] = []    # (audit_id, job) waiting
        self.current: str | None = None
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

    # ---- free checks ----
    def take_free(self) -> tuple[bool, str]:
        """Reserve one free quick check. Returns (ok, reason) with reason in cap|paid_first|quota."""
        with self.lock:
            st = self._load()
            if st["free_used"] >= free_cap():
                return False, "cap"
            waiting = len(self.queue) + (1 if self.current else 0)
            if waiting > max(0, paid_per_day() - st["paid_started"]):
                return False, "paid_first"
            if not gemini_quota_left():
                return False, "quota"
            st["free_used"] += 1
            self._save(st)
            return True, ""

    # ---- paid queue ----
    def enqueue(self, audit_id: str, job) -> bool:
        """job() -> "done" | "defer". Returns False if the audit is already queued or running."""
        with self.lock:
            if audit_id == self.current or any(a == audit_id for a, _ in self.queue):
                return False
            self.queue.append((audit_id, job))
            if not self.worker or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._run, name="paid-queue", daemon=True)
                self.worker.start()
            self.cv.notify_all()
            return True

    def busy(self, audit_id: str) -> bool:
        with self.lock:
            return audit_id == self.current or any(a == audit_id for a, _ in self.queue)

    def _run(self):
        while True:
            with self.lock:
                while not self.queue:
                    self.cv.wait(timeout=60)
                aid, job = self.queue.pop(0)
                self.current = aid
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
                    self.queue.insert(0, (aid, job))
            if out == "defer":
                self._wait_for_quota(force=True)

    def _wait_for_quota(self, force: bool = False):
        """Sleep until Gemini quota is back (re-checked every 10 minutes, at the latest just after the PT reset)."""
        first = True
        while force and first or not gemini_quota_left():
            first = False
            time.sleep(max(1.0, min(600.0, next_reset() + 120 - time.time())))

    def eta(self, audit_id: str | None = None) -> dict:
        """Honest delivery estimate for an order already queued (audit_id) or for a new order (None)."""
        with self.lock:
            ids = ([self.current] if self.current else []) + [a for a, _ in self.queue]
            ahead = ids.index(audit_id) if audit_id in ids else len(ids)
            st = self._load()
        now = time.time()
        today_left = max(0, paid_per_day() - st["paid_started"]) if gemini_quota_left() else 0
        slot = ahead + 1 - (1 if self.current and audit_id == self.current else 0)
        if audit_id and audit_id == self.current:
            ready = now + REPORT_MINUTES * 60
        elif slot <= today_left:
            ready = now + slot * REPORT_MINUTES * 60
        else:
            days = math.ceil((slot - today_left) / paid_per_day())
            ready = next_reset(now) + (days - 1) * 86400 + ((slot - today_left - 1) % paid_per_day() + 1) * REPORT_MINUTES * 60
        hours = max(0.0, (ready - now) / 3600)
        return {"queue_ahead": ahead, "eta_ts": int(ready), "eta_hours": round(hours, 1), "within_24h": hours <= 24,
                "eta_cn": cn_time(ready)}

    def status(self) -> dict:
        with self.lock:
            st = self._load()
            waiting = len(self.queue) + (1 if self.current else 0)
        cap = free_cap()
        return {"free_cap": cap, "free_left_today": max(0, cap - st["free_used"]), "paid_queue": waiting,
                "paid_reports_per_day": paid_per_day(), "quota_day_resets_cn": cn_time(next_reset()),
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
