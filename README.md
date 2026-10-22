# AI 可见度体检 / AI Visibility Audit — MVP

Audit a Shopify or independent store for AI-assistant visibility, then sell the **full report + fix pack ($19 USD one-off by card via Stripe; delivered within 24 h; refund within 7 days if not delivered in 24 h or not useful, once per customer)**.
Plan: `/workspace/projects/product_research/round1.md` (Shotgun, 2026-10-22). Tentative launch: Mon 2026-10-26.

> **Simulated mode.** The default engine in dev is a deterministic **stub**, so no API key or spending is needed.
> Every stub output is labelled SIMULATED: the report title, a top and bottom banner, the Markdown header, `audit.json` `simulated: true`, and `SIMULATED.txt` in the fix pack.
> With `AIV_ENV=production` the app **refuses** the stub engine. Samples in `samples/local-simulated/` are internal tests only, **not public samples**.
> Public sample audits must be regenerated with real engines once the API budget is approved.

> Public source, all rights reserved (see `LICENSE`). Live: https://audit.aiseenlab.com (site; old https://ai-visibility-audit-bvb.pages.dev redirects there once the domain is active) · backend on Render (see `docs/deploy.md`).

## What it does
| Part | Module | Notes |
|---|---|---|
| Crawler and discovery | `aiv/crawl.py`, `aiv/http.py` | GET only, at most 1 request/s per host, 15 s timeout, 3 MB cap. Detects Shopify, reads `/products.json`, falls back to homepage links, then the sitemap. Builds the store profile: name, currency, categories, socials, policies. |
| AI-crawler access | `aiv/checks.py` | Requests the homepage and 3 product pages with the official GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot and PerplexityBot UAs, plus a browser baseline. Detects 403s and challenges (Cloudflare, Akamai, DataDome and others). 429 gets one retry and counts as rate-limited, not blocked. Also checks content parity. |
| robots.txt | `aiv/checks.py` | Own RFC 9309 parser (groups, `*`, `$`, longest match). Covers 12 AI tokens and separates answer-time crawlers from training crawlers. |
| Structured data | `aiv/checks.py` | JSON-LD (`@graph`, lists, broken JSON, missing `@context`) and microdata. Product, Offer, AggregateRating and Organization are checked for required and recommended fields. Bare reference nodes are ignored. Type detection is cross-checked against validator.schema.org with `scripts/compare_validator.py`. |
| llms.txt / sitemap | `aiv/checks.py` | Detects Shopify's auto-generated generic `llms.txt`. |
| AI recommendation test | `aiv/visibility.py`, `aiv/llm/` | Generates buyer questions (5 for quick, 25 for full). Full mode samples ChatGPT 2×, Perplexity 2× and Gemini 3× per question by default (`--samples N` overrides with a uniform count). Measures the mention rate, share of voice, competitors AI names, and cited domains. |
| LLM providers (pluggable) | `aiv/llm/` | One `Provider` interface. Engines: `stub` (default, offline); `gemini` (free tier, Google Search grounding); `openai_compat` (Groq, OpenRouter free models and similar; no web search); `openai` (Responses API + web_search, paid); `perplexity` (sonar, paid). `CostMeter` logs every call and enforces a per-audit budget of $1.10 by default. |
| Fix pack | `aiv/fixes.py` | `robots.txt.snippet`, Shopify `robots.txt.liquid`, Product and Organization JSON-LD (per-product static files plus Shopify Liquid snippets, including review metafields), `llms.txt`, 5 content drafts (templates in stub mode, LLM-written with placeholders otherwise), Cloudflare allow-AI steps, README. Zipped. |
| Report | `aiv/report.py` | Bilingual HTML (zh/en toggle, plus separate zh and en files), Markdown, PDF via headless Chrome. The free report shows the top 6 findings plus an unlock CTA. The full report adds a per-question table, competitors, cited-source gaps and the fix-pack list. |
| Web (local only) | `aiv/server.py`, `landing/` | Static landing page ($19 USD by card via the live Stripe Payment Link, Stripe only; refund policy, FAQ), `terms.html`, `privacy.html`, `POST /api/check` (rate limit 3 per hour per IP, SSRF guard), report serving with a paywall (402 on the full report and zip until paid). Unlock routes: `GET /paid` (Stripe session verified via the API, `docs/stripe_setup.md`), optional Stripe webhook, `POST /admin/unlock` plus the `admin-unlock` CLI for comps and support cases (`docs/manual_unlock.md`), and `/dev/unlock` (dev only). |
| Retention | `scripts/purge_old.py` | Deletes `data/reports`, `data/emails`, `data/uploads` and `data/tmp`, plus old cost-log lines, after 7 days (`AIV_RETENTION_DAYS`). Run daily from cron. |
| Cost dashboard | `scripts/cost_report.py` | Summarises `logs/llm_calls.jsonl` per day, engine and audit, and flags audits over $1. |

## Run
```bash
cd /workspace/projects/ai-visibility-audit
pip install -r requirements.txt          # requests, beautifulsoup4, lxml (already on the box)

python3 -m aiv quick allbirds.com                       # free quick check -> data/reports/<id>/
python3 -m aiv full allbirds.com --out /tmp/allbirds    # full report + fixpack.zip + PDFs
python3 -m aiv full mystore.com --engine-mode gemini-only   # real engine, Gemini free tier, $0 (needs GEMINI_API_KEY)
python3 -m aiv full mystore.com --engine-mode full          # + ChatGPT / Perplexity if their keys exist; falls back to gemini-only otherwise
./scripts/gemini_real_run.sh quick https://store1.com ...   # Gemini-only real run, paid keys unset, --budget 0 -> samples/gemini-real/

cp -n config/dev.env.example config/dev.env; set -a; . config/dev.env; set +a; python3 -m aiv serve  # http://127.0.0.1:8787 (landing + API + dev unlock)

python3 scripts/purge_old.py --dry-run                  # retention job (7 days)
python3 scripts/cost_report.py                          # LLM cost dashboard
python3 scripts/estimate_cost.py                        # list-price estimate per full report at default sampling (no API calls)
python3 -m aiv rerender samples/local-simulated/full/allbirds.com   # re-score/re-render from audit.json
python3 scripts/compare_validator.py URL1 URL2 ...      # schema parity vs validator.schema.org
python3 scripts/batch_quick.py OUT_DIR store1.com store2.com ...   # timing check on many stores
```

## Engine modes
Used when no explicit `--engines` / `AIV_ENGINES` is given (always the case in production):
| Mode | Engines | When |
|---|---|---|
| `gemini-only` (default, fallback) | Gemini free tier | Free check (always), public samples, paid reports until paid keys exist |
| `full` | ChatGPT (if `OPENAI_API_KEY`) + Perplexity (if `PERPLEXITY_API_KEY`) + Gemini | Paid reports once keys are approved |
Missing keys degrade gracefully; every report has an "Engines used" line listing engines, models, whether answers used live search, and engines skipped.
Gemini knobs: `AIV_GEMINI_MODELS` (fallback chain), `AIV_GEMINI_GROUNDING=auto|on|off`, `AIV_GEMINI_RPM` (10), `AIV_GEMINI_THINKING` (minimal).
Free-tier facts measured on 2026-10-22: no search grounding for Gemini 3.x on the free tier; 20 requests/day per flash model; one full report = 80 calls. See `samples/gemini-real/README.md`.

## Deploy
Prepared, not published: see `docs/deploy.md` (GitHub Pages vs Cloudflare Pages, free Python hosting, Dockerfile, Render blueprint, `scripts/build_static.sh`).

## Tests
```bash
python3 -m unittest discover -s tests -t . -v
```
These run offline against `tests/fakeshop.py`, a fake Shopify store with a Cloudflare-style 403 challenge for PerplexityBot (simulating Bot Fight Mode), a robots.txt that blocks GPTBot, microdata, broken JSON-LD and a context-less JSON-LD block.
Covered: robots parsing, schema validation, providers (mocked HTTP), budget cap, production stub refusal, quick and full end to end, fix-pack contents, server paywall and unlock flow, Stripe signature, `/paid` verification and single use, admin unlock (auth, lockout, log, idempotency, CLI), rate limit, SSRF guard and the retention purge.

## Configuration / placeholders (pending approval)
| Placeholder | Where | Waiting on |
|---|---|---|
| `__AIV_DOMAIN__`, `support@__AIV_DOMAIN__` | `landing/config.js`, `landing/index.html` canonical, `aiv/http.py` contact URL, privacy page | Domain purchase and Cloudflare |
| `__PENDING_USD_19__` | `landing/config.js` | Stripe Payment Link (set at build time) |
| `STRIPE_RESTRICTED_KEY`, `AIV_STRIPE_PAYMENT_LINK_ID`, optional `STRIPE_WEBHOOK_SECRET` (Payment Link URL is already wired in) | env (`config/production.env.example`), steps in `docs/stripe_setup.md` | Stripe |
| `OPENAI_API_KEY` / `PERPLEXITY_API_KEY` | env | Paid engines (min prepaid: OpenAI $5, Perplexity $10). `GEMINI_API_KEY` is set on the box (free tier). |
| `__HOSTING_PROVIDER__` | privacy page | Hosting choice |

## Known limits
- The Bot Fight Mode check is verified on a simulated Cloudflare 403 (fixture). There is no real Cloudflare test site without an account. Cloudflare also verifies real crawlers by IP, so the report carries that caveat.
- Stack: static landing page plus a stdlib Python server (confirmed; no Next.js port).
- Real-engine runs so far: Gemini free tier only (4 quick checks + 1 full report, $0, `samples/gemini-real/`). ChatGPT/Perplexity untested (no keys). The Perplexity provider still targets Sonar chat completions; Perplexity's changelog says Sonar Chat Completions moved to the Agent API (July 2026), so re-check that endpoint before enabling `full` mode.
- Default full sampling is 25 questions, ChatGPT × 2, Perplexity × 2 and Gemini × 3 (175 calls). The list-price estimate is about $1.01 per report, within the default per-audit cap of $1.10 (see ACCEPTANCE.md #4 and `scripts/estimate_cost.py`).
