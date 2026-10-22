# Live deployment — 2026-10-22 (UTC+8)

| Part | URL | Host / plan |
|---|---|---|
| Landing + privacy | https://ai-visibility-audit-bvb.pages.dev (privacy: `/privacy`) | Cloudflare Pages project `ai-visibility-audit`, direct upload of `site/` |
| Backend API | https://ai-visibility-audit-api.onrender.com (health: `/healthz`) | Render free web service `ai-visibility-audit-api` (Docker, Singapore), `srv-dbcpdg29lscs73em5hsg` |
| Source | https://github.com/ohomygood/ai-visibility-audit | public, all rights reserved (`LICENSE`) |

State (updated 2026-10-22 13:25 UTC+8): **payments live**.
- `AIV_ENV=production`, engines are Gemini only (free tier). The stub is refused and `/dev/unlock` returns 404.
- `STRIPE_RESTRICTED_KEY` (`rk_live`, read-only) and `AIV_STRIPE_PAYMENT_LINK_ID=plink_1UTDOTJvue1A936QJOyknFEh` are set on Render.
  `/healthz` shows `"payments_configured": true`. Fake sessions are rejected (`session_not_found`).
  `AIV_ORDER_LINK_SECRET` is set for signed admin order links.
- Payment Link (verified read-only via the API): active, 1900 USD × 1, promo codes off, after-completion redirect to the `/paid` URL below.
- Key permissions (verified): Read on Payment Links, Checkout Sessions, Charges, Balance transactions and PaymentIntents. Customers is denied.
- **Stripe only.** WeChat Pay was removed from every page (2026-10-22 13:40 UTC+8). There's no QR, no ¥ price and no screenshot instructions. `scripts/check_release.py` fails the site build if a QR or WeChat copy appears.

## Stripe settings (done; for reference)

- Payment Link `https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02` → After payment → redirect to:
  `https://ai-visibility-audit-api.onrender.com/paid?session_id={CHECKOUT_SESSION_ID}`
- Optional webhook: `https://ai-visibility-audit-api.onrender.com/stripe/webhook`.
  Events: `checkout.session.completed`, `checkout.session.async_payment_succeeded`.
- Restricted key: **Checkout Sessions: Read** at minimum. Charges: Read and Balance transactions: Read are optional, for the cashier; the server ignores them. `sk_` keys are refused.
- Render env vars to set (names only): `STRIPE_RESTRICTED_KEY`, `AIV_STRIPE_PAYMENT_LINK_ID` (the `plink_…` id), and optionally `STRIPE_WEBHOOK_SECRET`.
  Then redeploy (Render → Manual Deploy) and check that `/healthz` shows `"payments_configured": true`.

## Rebuild and redeploy

```bash
AIV_SITE_URL=https://ai-visibility-audit-bvb.pages.dev AIV_API_BASE=https://ai-visibility-audit-api.onrender.com \
AIV_STRIPE_PAYMENT_LINK=https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02 ./scripts/build_static.sh
CLOUDFLARE_ACCOUNT_ID=<account> npx wrangler pages deploy site --project-name ai-visibility-audit --branch main
```

Render: auto-deploy is off. Push to GitHub `main`, then press **Manual Deploy** (or call `POST /v1/services/<id>/deploys`).

## Caveats (Render free)

- **Cold start:** the service spins down after 15 min idle. The first request then takes about 30–60 s, and a free check adds about 60 s on top.
  The landing page shows "约 1–2 分钟", so a cold first check can take about 2 minutes.
- **Ephemeral disk:** reports, `data/stripe/used_sessions.jsonl` and `data/admin/*.jsonl` are lost on every restart or redeploy.
  **Paid orders still survive.** Stripe orders rebuild from the session (`/order/<id>?s=…`). Manual unlocks rebuild from
  the signed order link (`?u=…`) or from the `ADMIN_AUDIT` log (`python3 -m aiv admin-reapply`). See docs/recovery.md.
  Free quick reports don't survive.
- **Free-check rate limit** (3/h per IP) keys on `X-Forwarded-For`. It is per-instance memory and resets on restart.
- **Gemini free tier:** quota falls back through the model chain, e.g. `gemini-3.5-flash` → `-lite`. It answers without live search when grounding quota runs out, and the report labels this.

# Deploy plan (historical notes from before launch) — 2026-10-22

## What runs where
| Part | Needs | Files |
|---|---|---|
| Landing + privacy (static) | static host | `landing/` → `scripts/build_static.sh` → `site/` |
| Python backend: free check API, report serving, paywall, Stripe webhook, 7-day purge | long-running Python + Chromium (JS-render check, PDF) | `aiv/server.py`, `deploy/backend/Dockerfile`, `deploy/backend/render.yaml` |
| Product #2 license Worker | Cloudflare Workers + KV | `../x-engagement-extension/worker/` |

## Static host: GitHub Pages vs Cloudflare Pages
| | GitHub Pages (existing ohomygood account) | Cloudflare Pages |
|---|---|---|
| New accounts | **0** | 1 (Cloudflare, free, no card) — the same account also hosts product #2's Worker |
| Cost | $0 (public repo; Pages from a private repo needs a paid plan) | $0 |
| Limits | 1 GB site, 100 GB/month soft bandwidth, 10 builds/h | 500 builds/month, unlimited static bandwidth |
| **Commercial use** | **Not allowed as free hosting for an online business / site primarily facilitating commercial transactions or commercial SaaS** (GitHub Pages limits + Terms for Additional Products, effective 2026-08-27) | Allowed on the free plan |
| Custom domain | yes | yes |
| Source | https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits · https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features | https://developers.cloudflare.com/pages/platform/limits/ |

**Recommendation:** GitHub Pages is the zero-account option but a $19 paid-report landing page is exactly the
"commercial SaaS" use GitHub's terms exclude, so it risks takedown. Use it at most for a short non-commercial
preview; for launch use **Cloudflare Pages** (one free account, which product #2 needs anyway).
Prepared: `deploy/github-pages/pages.yml` (manual-dispatch workflow, not installed), `deploy/cloudflare-pages/`.

## Python backend: free options
| Option | Account | Fits? |
|---|---|---|
| **Render free web service** (Docker) | Render (sign in with GitHub, no card) | Works for MVP. Spins down after 15 min idle (~1 min cold start), 750 instance-h/month, **ephemeral disk**: reports vanish on spin-down/redeploy, so paid reports need external storage (e.g. Cloudflare KV/R2) before taking money. Render says free instances are not for production. https://render.com/docs/free |
| Cloudflare Workers (Python) | Cloudflare | No: no headless Chrome, 10 ms CPU on free plan; fine for the Stripe webhook only. |
| GitHub Actions as backend | 0 | No: Actions terms exclude using runners as a serverless app backend. |
| Google Cloud Run free tier | Google Cloud billing account (card) | Technically free at low volume but needs a card on file. |
| Hugging Face Spaces | HF account | Docker Spaces now need a paid plan. |

**Fewest accounts with $0:** Cloudflare (Pages + Worker + KV) **+** Render (Python backend) = 2 new free accounts,
no card. Zero-new-account path does not exist for the backend.

## Steps once approved (do not run before)
1. `AIV_SITE_URL=… AIV_API_BASE=… AIV_STRIPE_PAYMENT_LINK=… ./scripts/build_static.sh` (refuses placeholders).
2. Cloudflare: `npx wrangler pages deploy site --project-name aiv-landing`.
3. Render: New → Blueprint → this repo → `deploy/backend/render.yaml`; set `GEMINI_API_KEY`, `AIV_STRIPE_WEBHOOK_SECRET`, `AIV_STRIPE_PAYMENT_LINK`; set `AIV_CORS_ORIGINS` to the Pages origin.
4. Stripe webhook → `https://<backend>/api/stripe/webhook`.

## 2026-10-22 18:30 (UTC+8): cleanup
- `audit.json` (served at `/r/<id>/audit.json`) no longer exposes server paths: `outputs` and `fixpack.dir/zip` are file names relative to the report dir (`report.en.html`, `fixpack.zip`). The CLI summary prints the absolute `out_dir` separately.
- No `AIV_WECHAT_ENABLED` switch exists in code or config (test `test_no_wechat_config_switch`); the leftover Render env var is unused.
