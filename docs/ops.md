# 运维说明 / Ops notes (MVP)

## Processes
- Web/API: `python3 -m aiv serve --host 127.0.0.1 --port 8787` behind a reverse proxy (Cloudflare) — **not deployed**.
- Retention: the server runs `scripts/purge_old.py` at startup and every 24 h (Render free has no cron). On a VPS a cron
  (`17 3 * * * cd /srv/ai-visibility-audit && python3 scripts/purge_old.py`) works too.

## Capacity: free cap + paid queue (`aiv/capacity.py`)
- Gemini free tier, measured 2026-10-22: gemini-3.5-flash and 3.6-flash 20 requests/day each; gemini-3.5-flash-lite
  100+/day without a cap (limit undisclosed). Quick check = 5 calls; full report = 80 calls (75 answers + 5 drafts).
  Planning budget ≈ 240 calls/day. Quota day = midnight US Pacific (15:00 or 16:00 Beijing time).
- Paid first: `AIV_PAID_REPORTS_PER_DAY=2` (160 calls) is reserved. Free: `AIV_FREE_DAILY_CAP=15` (75 calls). Free checks
  also pause while paid orders wait beyond today's capacity, or when every Gemini model is out of quota. Users then see
  "今天的免费检测名额已用完，请明天再试".
- Paid reports run one at a time from a queue. If Gemini runs out of quota mid-report (>20% unanswered), the partial
  result is discarded and the report is retried first after the reset. It is never delivered half-empty.
  The order page shows queue position and ETA; the landing page shows the ETA before purchase when it is over 24 h.
- Restarts: pending paid orders are re-queued at startup from `data/reports/*/paid.json`; after a disk wipe the buyer's
  order link (`/order/<id>?s=<session>`) re-verifies with Stripe and re-queues. The free counter resets with a wipe; Gemini's
  own quota is the backstop.
- `GET /api/capacity` shows free checks left, queue length and the ETA for a new order.
- Production guard: with `AIV_ENV=production` the server refuses to start with the stub/mock engine, without a Gemini key,
  or with `AIV_DEV_UNLOCK=1`. Simulated output is also refused per report.
- Production env: copy `config/production.env.example` → `config/production.env`; `AIV_ENV=production` refuses the stub engine.

## Cost dashboard (MVP = log file)
- Every LLM call → `logs/llm_calls.jsonl` (audit id, engine, model, tokens, cost, latency, error).
- Summary: `python3 scripts/cost_report.py` (per day / per engine / per audit; flags audits over $1).
- Per-audit hard cap: `CostMeter(budget_usd=1.10)` (CLI `--budget`, default 1.10) — calls stop once the next call would exceed the cap.

## Politeness / safety
- Only GET; ≥1 s between requests per host; 15 s timeout; 3 MB body cap; identifies itself in the default UA.
- Bot probes send each vendor's official UA string (needed to test blocking). Real crawlers are verified by IP,
  so a 403 on Cloudflare sites is reported with that caveat.
- SSRF guard on the public API: private, loopback, link-local and reserved IPs are refused.
  Redirects are followed manually and every hop is re-checked (AIV_BLOCK_PRIVATE=1, on by default in `serve` and in production).
- Free API rate limit: 3 checks / hour / IP (in-memory; use Cloudflare rate limiting in prod).

## Manual unlock (comps / support) / 手动解锁
- Cashier runbook: `docs/manual_unlock.md` (curl, or `python3 -m aiv admin-unlock … [--remote <RENDER_URL>]`).
- `ADMIN_TOKEN` (`openssl rand -hex 32`, at least 32 chars) is stored only as a Render secret. Unset means the endpoint is off (404). Lockout: 5 failures per IP or 20 in total per 15 min.
- Logs: `data/admin/unlocks.jsonl` (append-only) and `data/admin/security.jsonl` (failed auth, IP only), plus `ADMIN_AUDIT {json}` lines on stdout, which survive in Render logs. Not purged. Optional, unscheduled export: `scripts/export_admin_log.py`.

## Stripe key scope
- The server needs only **Checkout Sessions: Read** (`rk_…`). A restricted key that also has other read-only permissions (e.g. Charges, Balance transactions) works the same, because only `GET /v1/checkout/sessions/{id}` is called (tests/test_admin.py).
- Any key that isn't `rk_` (`sk_…` full secret keys, `pk_…`) is refused and nothing unlocks.
