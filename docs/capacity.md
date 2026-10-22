# Daily capacity (Gemini free tier) and the sold-out throttle

Updated 2026-10-22 (UTC+8). Production runs `AIV_ENGINE_MODE=gemini-only` with `AIV_GEMINI_MODELS` unset.

## Calls per product (from the code)

| Run | Code | Gemini calls |
|---|---|---|
| Free quick check | `run_audit(mode="quick")` → `run_visibility(n_questions=5, samples=1)`, Gemini only | 5 × 1 = **5** |
| Paid full report | `run_visibility(n_questions=25, samples=DEFAULT_FULL_SAMPLES["*"]=3)` + `content_drafts` (5 `DRAFT_SPECS`) | 25 × 3 + 5 = **80** |

Measured on 2026-10-22 (`samples/gemini-real/README.md`): quick checks used 5 calls, the allbirds.com full report
75 answers + 5 drafts = 80.

## Daily quota per model (free tier, per project, resets midnight America/Los_Angeles = 15:00 Beijing in PDT, 16:00 in PST)

Model rotation (`GeminiProvider.DEFAULT_MODELS`): a per-day 429 marks the model exhausted and moves to the next.

| Model | Requests/day | Source |
|---|---|---|
| gemini-3.5-flash | 20 | 429 body `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, quota value 20 |
| gemini-3.6-flash | 20 | same, quota value 20 |
| gemini-3.5-flash-lite | ≥ 100 (not disclosed) | 100 requests in one day without a 429; counted as **100** (a floor, not the limit) |
| gemini-flash-lite-latest | 0 counted | alias of the latest Flash-Lite (`models.get`: "Latest release of Gemini Flash-Lite"), assumed to share its quota |

Google's rate-limit page publishes no free-tier numbers (only "view your active rate limits in AI Studio"), and the
box key is the same project as production, so the Flash-Lite limit was **not** probed by exhausting it (that would
take today's capacity away from customers).

## Budget

```
firm daily budget      = 20 + 20 + 100                      = 140 calls
reserve ~10 %          = retries, the grounding probe after each cold start (auto mode), 5xx retries
usable                 ≈ 126 calls
paid: 1 report         = 80 calls          (2 reports = 160 > 140: not supported)
free: 6 checks x 5     = 30 calls
total                  = 110 calls  <= 126   (16 spare)
```

So **AIV_PAID_REPORTS_PER_DAY=1** and **AIV_FREE_DAILY_CAP=6**. Free checks also pause while a paid order waits
for today's slot, and stop when every model is out of quota. Raise the numbers only after the Flash-Lite limit
is known (AI Studio → Rate limits, or a 429 with its quota value in the Render log).

## Sold out

* `GET /api/capacity` (public, CORS for the site origins) returns `paid_slots_left_today`, `sold_out`,
  `next_slot_cn`, `quota_day_resets_cn`, `paid_queue`, `free_left_today` and the ETA of a new order.
* Today's booked slots = max(local count, Stripe schedule). The Stripe schedule (`aiv/slots.py`) lists the
  complete Checkout Sessions of our Payment Link (last 14 days; refunded ones dropped) and gives each one the first
  quota day, on or after its payment, with a free slot, oldest payment first. Same input → same schedule, so a
  wiped disk doesn't reopen a sold-out day.
* The site hides the buy buttons when `sold_out` and shows
  "今日名额已售罄，明天开放下一个名额 / Today's slots are sold out, next slot opens tomorrow" with the next slot time.

## Orders paid while sold out (direct Stripe link)

* `/paid` verifies the session as before, books its day from the Stripe schedule (stored in `paid.json` as `day`)
  and queues it FIFO. The worker never starts an order before its day, nor more than the daily cap.
* `/order/<id>` says the day was sold out, shows the queue position and the ETA (Beijing time). When the ETA is
  more than 24 hours away it also offers the full refund within 7 days.
* Restarts / wiped disk: on startup and every 10 minutes `sync_from_stripe()` re-queues, from Stripe alone, the
  orders booked for a future day (held) and those booked for today whose slot opened less than
  `AIV_SYNC_WINDOW_MIN` (90) minutes ago. Something must wake the server at the quota reset and keep it awake
  through that window: `deploy/github-actions/quota-day-keepalive.yml` (pings every 4 minutes for ~100 minutes from
  each reset; free on a public repo). **Not active yet**: it has to be copied to `.github/workflows/` with a GitHub
  token that has the `workflow` scope. Until then, any visit in the window (or the buyer's order link) wakes it.
* Older bookings are not regenerated automatically (that would burn a day's quota on every cold start). The
  buyer's order link (`/order/<id>?s=<session>`) always re-verifies and re-queues the order, and the /order page
  asks buyers to bookmark it.
