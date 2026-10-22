# 退款流程 / Refund runbook (7 天 / 7 days)

Policy shown on the landing page and terms.html (2026-10-22): within 7 days of purchase, a full $19 refund if the
report wasn't delivered within 24 hours of payment, or if the buyer found it not useful. **Once per customer** (check the
customer's email / card in Stripe for an earlier refund). We refund proactively if generation fails or the site can't be
reached. Support and refunds: go_with_prob@mail.grokbot.com.

## Customer-requested refund
1. Customer emails support with the report ID (format `YYYYMMDD-HHMMSS-xxxxxx`).
2. Look up `data/reports/<id>/paid.json` → `source` = `stripe:<checkout_session_id>`.
   (Data is deleted after 7 days, so the Stripe dashboard is the source of truth after that.)
3. Stripe Dashboard → Payments → search the session/customer email → **Refund** → full amount.
   (cashier owns Stripe access; pigu never touches live keys.)
4. Reply to the customer: refund issued, 5–10 business days to appear.
5. Optional: delete the report early: `rm -rf data/reports/<id>`.

## Automatic refund (generation failed)
- `GET /api/status/<id>` shows `full_error: true`, or `data/reports/<id>/full_error.txt` exists.
- Ops check (daily, part of the retention cron): list paid reports with `full_error.txt` → refund in Stripe → email customer.

## Test mode
With Stripe test keys: create a test Payment Link with `client_reference_id` passthrough, pay with card 4242 4242 4242 4242,
confirm webhook `checkout.session.completed` → report unlocks; then refund from the test dashboard.
Status: **blocked** until Stripe HK access is approved (see README "Pending approval").
