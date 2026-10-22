# Cloudflare Pages (prepared, not deployed)
Needs ONE new account: Cloudflare (free plan, no card). The same account also hosts product #2's license Worker.
```
AIV_SITE_URL=https://<project>.pages.dev AIV_API_BASE=https://<backend> AIV_STRIPE_PAYMENT_LINK=<link> ./scripts/build_static.sh
npx wrangler pages deploy site --project-name aiv-landing     # <- publishes; needs approval
```
`deploy/cloudflare-pages/_headers` is copied into site/ by hand before deploy (security headers).
