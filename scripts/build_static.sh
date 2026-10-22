#!/usr/bin/env bash
# Assemble the static landing into site/ for a static host. Substitutes placeholders from env; refuses to
# build with placeholders left unless ALLOW_PLACEHOLDERS=1 (preview builds). Publishes NOTHING.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${AIV_SITE_URL:=https://__AIV_DOMAIN__}" "${AIV_API_BASE:=}" "${AIV_STRIPE_PAYMENT_LINK:=https://buy.stripe.com/__PENDING_USD_19__}"
rm -rf site && mkdir -p site && cp landing/*.html landing/*.js landing/*.css site/   # text assets only, never images
cp deploy/cloudflare-pages/_headers site/_headers
# AI Seen Lab brand (favicon + footer mark): byte-for-byte copies of brand/; check_release.py allows nothing else
mkdir -p site/brand && cp brand/aiseenlab_icon.svg brand/aiseenlab_icon.png brand/favicon-16.png brand/favicon-32.png brand/apple-touch-icon.png brand/favicon.ico site/brand/ && cp brand/favicon.ico site/favicon.ico
# pages.dev -> custom domain redirect, only when the site URL is a custom domain (no self-redirect loop)
case "$AIV_SITE_URL" in *.pages.dev*|*__AIV_DOMAIN__*) ;; *) cp deploy/cloudflare-pages/_worker.js site/_worker.js ;; esac
python3 - "$AIV_SITE_URL" "$AIV_API_BASE" "$AIV_STRIPE_PAYMENT_LINK" <<'PY'
import sys, pathlib, re
site, api, pay = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
dom = re.sub(r"^https?://", "", site)
for p in pathlib.Path("site").glob("*"):
    if p.suffix in (".html", ".js", ".css"):
        t = p.read_text(encoding="utf-8").replace("https://__AIV_DOMAIN__", site).replace("__AIV_DOMAIN__", dom)
        if p.name == "config.js":
            t = re.sub(r'API_BASE:\s*"[^"]*"', f'API_BASE: "{api}"', t)
            t = re.sub(r'https://buy\.stripe\.com/__PENDING_USD_19__', pay, t)
        p.write_text(t, encoding="utf-8")
PY
touch site/.nojekyll
# Stripe only: no payment QR, no WeChat copy may ever ship in the site (fails the build).
python3 scripts/check_release.py site
left=$(grep -rlI "__AIV_DOMAIN__\|__PENDING" site || true)
if [ -n "$left" ] && [ "${ALLOW_PLACEHOLDERS:-0}" != "1" ]; then
  echo "placeholders still present in: $left (set AIV_SITE_URL / AIV_API_BASE / AIV_STRIPE_PAYMENT_LINK, or ALLOW_PLACEHOLDERS=1 for a preview)"; exit 2
fi
echo "site/ ready ($(ls site | wc -l) files)"
