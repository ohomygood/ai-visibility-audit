"""Paid-report slots that survive a wiped disk: the day schedule is rebuilt from Stripe alone.

Render free loses the disk on every restart, redeploy and idle spin-down, so local counters can't be the only
source of truth for "how many paid reports are booked today". Every paid order is a complete Checkout Session on
our Payment Link, so the server lists those sessions (read-only rk_ key, Checkout Sessions: Read) and assigns them
to quota days deterministically, oldest payment first:

    each session goes to the first quota day >= the day it was paid that still has a free slot
    (AIV_PAID_REPORTS_PER_DAY slots per day).

The same list and the same rule give the same schedule after any restart, so the sold-out state and every
order's delivery day are stable. Refunded payments (latest charge refunded / amount_refunded > 0) free their slot.
If Stripe can't be reached, the last good schedule is kept and the local counters still apply.
"""
from __future__ import annotations

import os
import threading
import time

import requests

from . import capacity, payments

LIST_API = "https://api.stripe.com/v1/checkout/sessions"
TTL = 120.0                     # seconds a fetched schedule is reused
LOOKBACK_DAYS = 14


class Book:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: list[dict] = []       # verified, not refunded: {"id","created","audit_id","domain"}
        self.fetched_at = 0.0
        self.ok = False
        self.error = ""

    # ---- Stripe ----
    def _fetch(self) -> list[dict]:
        key = payments.restricted_key()
        link = os.environ.get("AIV_STRIPE_PAYMENT_LINK_ID", "")
        if not key.startswith("rk_") or not link.startswith("plink_"):
            raise RuntimeError("not_configured")
        out, after = [], None
        since = int(time.time() - LOOKBACK_DAYS * 86400)
        for _ in range(20):                                   # <= 2000 sessions in the window
            params = [("payment_link", link), ("status", "complete"), ("limit", "100"), ("created[gte]", str(since)),
                      ("expand[]", "data.payment_intent.latest_charge")]
            if after:
                params.append(("starting_after", after))
            r = requests.get(LIST_API, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"stripe HTTP {r.status_code}")
            d = r.json()
            for s in d.get("data", []):
                try:
                    aid, dom = payments.verify_session_ref(s)
                except payments.PaymentError:
                    continue
                pi = s.get("payment_intent")
                ch = pi.get("latest_charge") if isinstance(pi, dict) else None
                if isinstance(ch, dict) and (ch.get("refunded") or (ch.get("amount_refunded") or 0) > 0):
                    continue
                out.append({"id": s["id"], "created": int(s.get("created") or 0), "audit_id": aid, "domain": dom})
            if not d.get("has_more") or not d.get("data"):
                break
            after = d["data"][-1]["id"]
        return out

    def refresh(self, force: bool = False) -> bool:
        if not force and time.time() - self.fetched_at < TTL:
            return self.ok
        try:
            got = self._fetch()
        except Exception as e:  # noqa: BLE001
            with self.lock:
                self.fetched_at, self.error = time.time(), type(e).__name__ if not isinstance(e, RuntimeError) else str(e)
            return self.ok
        with self.lock:
            self.sessions, self.fetched_at, self.ok, self.error = got, time.time(), True, ""
        return True

    def add(self, sess: dict) -> None:
        """A session just verified by /paid or the webhook (the list endpoint may lag a few seconds)."""
        with self.lock:
            if not any(s["id"] == sess["id"] for s in self.sessions):
                self.sessions.append(sess)

    # ---- schedule ----
    def schedule(self, per_day: int | None = None) -> dict:
        """session id -> (quota day, slot index on that day), oldest payment first."""
        per_day = per_day or capacity.paid_per_day()
        with self.lock:
            ss = sorted(self.sessions, key=lambda s: (s["created"], s["id"]))
        used: dict[str, int] = {}
        out = {}
        for s in ss:
            day = capacity.quota_day(s["created"])
            while used.get(day, 0) >= per_day:
                day = capacity.next_day(day)
            out[s["id"]] = (day, used.get(day, 0))
            used[day] = used.get(day, 0) + 1
        return out

    def booked(self, day: str) -> int:
        return sum(1 for d, _ in self.schedule().values() if d == day)

    def by_day(self) -> dict:
        res: dict[str, int] = {}
        for d, _ in self.schedule().values():
            res[d] = res.get(d, 0) + 1
        return res

    def session(self, sid: str) -> dict | None:
        with self.lock:
            return next((s for s in self.sessions if s["id"] == sid), None)


BOOK = Book()
