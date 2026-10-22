# Deploy plan (prepared, NOTHING published) — 2026-10-22

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
