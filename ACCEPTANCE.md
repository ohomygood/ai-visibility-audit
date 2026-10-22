# Acceptance status: MVP, 2026-10-22 (UTC+8)

Criteria from Shotgun's plan (Page "产品机会调研 · 第一轮", section 方案一 交付物和验收标准), plus the steering requirements from 10:00.

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Free quick check under 60 s, tested on 10 real stores | **Stub: PASS (10/10). Real Gemini: PARTIAL (1/4 under 60 s; 36–82 s)** | `samples/local-simulated/quick/quick_timing.json`. Quick checks took 18–54 s at 1 request/s per store. Full mode, including 225 stub calls, fix pack and PDF, took 36–44 s. Through the local API it took 44.8 s. bombas.com returned 429 to the box IP, so its score is withheld and the report says so. **Real engine (2026-10-22, Gemini free tier):** allbirds 82 s, deathwishcoffee 82 s, colourpop 36 s, gymshark 66 s (`samples/gemini-real/`). The two 82 s runs include one-off quota probing that is now cached; the LLM now runs in parallel with the crawl, so the floor is the ~28–36 s crawl plus free-tier pacing (10 RPM). Landing copy changed from "60 秒" to "约 2 分钟 / about 2 minutes". |
| 2 | A Cloudflare Bot Fight Mode site is correctly reported as 403 | **PARTIAL** | Passes against a simulated Cloudflare 403 challenge (`tests/fakeshop.py`, `test_quick`). There is no real Bot Fight Mode site to test, because that needs a Cloudflare account and domain (pending). None of the 10 real stores blocked the probes. |
| 3 | On 5 sample pages, schema results match validator.schema.org | **PASS on static HTML (6/6); 6/8 overall** | `samples/validator_comparison.json`, `scripts/compare_validator.py`. The 2 mismatches are brooklinen pages that inject JSON-LD with JavaScript. Validator render timing differs, and on one page it saw no Product at all. The audit now detects this case and reports JS-only schema as a finding, because most AI crawlers don't run JS. The check also found a real Allbirds issue: an AggregateRating block with no `@context`, which the validator also ignores. |
| 4 | Paid report generated within 10 minutes, API cost under $1 per report, logged | **Time: PASS with a real engine (Gemini-only full report 8.6 min). Cost: ~$1.01 by estimate, 1¢ over the plan's $1; within the new $1.10 cap. Logging: PASS** | Default full-report sampling (set 2026-10-22 10:33 UTC+8) is 25 questions, **ChatGPT × 2, Perplexity × 2, Gemini × 3**, which makes 175 engine calls (`DEFAULT_FULL_SAMPLES` in `aiv/visibility.py`). The per-audit spending cap now defaults to **$1.10** (`CostMeter`, `run_audit`, CLI `--budget`). `python3 scripts/estimate_cost.py` at Oct-2026 list prices gives **$1.011** per report: ChatGPT 50 calls $0.711, Perplexity 50 calls $0.287, Gemini 75 calls $0 on the free tier (but the free tier has no search grounding for Gemini 3.x and only 20 requests/day per flash model, so this is ungrounded and about one full report/day), content drafts $0.013. On Gemini's paid tier, Gemini 3+ grounding is 5,000 free searches/month then $14 per 1,000 search queries (https://ai.google.dev/gemini-api/docs/pricing). Real Gemini-only full report (allbirds.com, 2026-10-22): 75/75 answers, 0 errors, 80 calls incl. drafts, **513 s**, **$0.00**. ChatGPT/Perplexity timing still unmeasured. Every call is logged to `logs/llm_calls.jsonl`, the cap stops the run before it overspends, and `scripts/cost_report.py` is the dashboard. Paid-engine cost still unconfirmed (no keys). |
| 5 | Stripe test-mode payment unlocks the report; refund process documented | **BLOCKED (Stripe) / code ready** | Payment Link placeholders are in place, using `client_reference_id = audit_id`. The webhook verifies the Stripe v1 signature (unit tested) and runs the full audit on `checkout.session.completed`. Paywall returns 402 until paid, and the dev-unlock flow is tested both live and in the test suite. Refund runbook: `docs/refund.md`. A real test-mode payment needs Stripe HK access. |
| 6 | Official APIs only; no scraping of chatgpt.com and similar | **PASS** | Only `aiv/llm/providers.py` talks to engines: OpenAI Responses, Perplexity, Gemini and OpenAI-compatible APIs. |
| 7 | Deliver repo, deploy URL, ops notes and cost dashboard | **PARTIAL** | Repo: this folder, as a local git repo. Ops: `docs/ops.md`. Cost dashboard: `scripts/cost_report.py`. Deploy URL: **not deployed** (domain, Cloudflare and hosting pending; no public deploy by instruction). |
| S1 | Stub is clearly labelled and off in production; local samples are not public | **PASS** | Simulated output is labelled in the HTML title, top and bottom banners, Markdown header, `audit.json` `simulated:true`, and `fixpack/SIMULATED.txt`. `AIV_ENV=production` refuses stub and has no default engine (`test_production_refuses_stub`). Samples live in `samples/local-simulated/`, whose README says they are internal and not public. |
| S2 | Privacy page plus 7-day retention job covering crawls, reports and emails | **PASS (job written; cron not installed)** | `landing/privacy.html`, and `scripts/purge_old.py` for `data/reports`, `data/emails`, `data/uploads`, `data/tmp` and old cost-log lines (`test_purge`). The cron line is in `docs/ops.md` and gets installed at deploy time. |
| S3 | Landing page shows $19 USD one-off (Stripe checkout may show local currency) and the refund policy (7 days: not delivered in 24 h or not useful, once per customer) | **PASS** | `landing/index.html` (#pricing, #refund), checked in `test_server_flow`. |
| S4 | Engine mode switch: `gemini-only` default/fallback, `full` adds ChatGPT/Perplexity when keys exist; report labels engines used | **PASS** | `aiv/llm/__init__.py` `resolve_mode`/`select_engines`, CLI `--engine-mode`, env `AIV_ENGINE_MODE`; free check pinned to gemini-only; production with no engine list uses gemini-only. Report "Engines used" line lists engine, models, live-search or not, and skipped engines (`test_engine_modes`). |
| S5 | Free static deploy prepared, not published | **PREPARED** | `docs/deploy.md` (GitHub Pages vs Cloudflare Pages; GitHub's terms bar Pages as free hosting for commercial SaaS), `scripts/build_static.sh`, `deploy/` (Pages workflow, Cloudflare headers, Dockerfile, Render blueprint), CORS allow-list `AIV_CORS_ORIGINS`. |

## Not built yet (plan items outside this MVP pass)
- Real-engine runs. They need an API key; a free Gemini key would let us test without spending.
- Next.js front end: not planned. Shotgun confirmed the static landing page plus Python stack (2026-10-22).
- Email delivery. Not needed: delivery happens on the report page.
- `$9/月` weekly re-test, planned for phase 2.

## Stripe Payment Link wiring (local only, 2026-10-22)

| Criterion | Status |
|---|---|
| Buy buttons (report paywall, landing after a free check, `/order` locked page) use the live link + `?client_reference_id=<audit_id>` (+ `prefilled_email` when known) | PASS (tests/test_payments.py, test_e2e.py) |
| `GET /paid?session_id=` retrieves the session with read-only `STRIPE_RESTRICTED_KEY`, checks complete/paid, link id, 1900 usd (both Adaptive Pricing shapes), audit id, single use | PASS (mocked Stripe API) |
| Webhook `/stripe/webhook` (alias `/api/stripe/webhook`) is optional, signature-checked, re-verified via API, same ledger → unlocks once | PASS |
| No `rk_` key or no `plink_` id → nothing unlocks; `sk_` keys refused; `/dev/unlock` disabled when `AIV_ENV=production` | PASS |
| Real Stripe round trip | PENDING: needs the owner's dashboard steps (docs/stripe_setup.md) + a Render URL |

## Manual WeChat unlock (local only, 2026-10-22)

| Criterion | Status |
|---|---|
| `POST /admin/unlock` (Bearer `ADMIN_TOKEN`, ≥32 chars, constant-time; disabled → 404; lockout 5/IP and 20 global per 15 min; token never logged) | PASS (tests/test_admin.py) |
| `python3 -m aiv admin-unlock` local (runs the report synchronously) and `--remote` | PASS |
| Append-only `data/admin/unlocks.jsonl` with all fields; idempotent (no-op logged); same `paid.json` + report trigger as `/paid` | PASS |
| zh copy: $19 by card plus ¥129 WeChat (¥99 early-bird toggle, off by default); QR is a gitignored local copy; contact is a placeholder | PASS; contact go_with_prob@mail.grokbot.com |
| `rk_` key with extra read-only permissions works; `sk_`/`pk_` refused | PASS |

## Stripe only (2026-10-22 13:40 UTC+8)

WeChat Pay UI removed from the landing page, report paywall and order pages. The admin unlock path is kept for comps and support. `tests/test_admin.py::TestStripeOnlyCopy` and `scripts/check_release.py` (run by `build_static.sh`) fail on WeChat copy or any QR image.
