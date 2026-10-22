# Stripe setup for AI 可见度体检 (for the owner)

Chinese buyers can also pay by WeChat QR and get unlocked by hand: see `docs/manual_unlock.md`.

What's already wired in the code: the live Payment Link
`https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02` ($19 one-off, Adaptive Pricing on).
Every buy button opens it as `…?client_reference_id=<audit_id>` (plus `&prefilled_email=` when we know the email),
so each payment maps to one audit.

What you need to do in the Stripe Dashboard: 3 required steps and 1 optional step, about 10 minutes.
Replace `<RENDER_URL>` with the backend URL once it exists, e.g. `https://aiv-api.onrender.com`. No trailing slash.

> Do the steps in **live mode**. To try them first in a **sandbox or test mode**, repeat them there too.
> The test-mode link, key and secret are separate values.

## 1. After-payment redirect on the Payment Link

1. Dashboard → **Payment Links** → click the $19 link (`buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02`) → **Edit** (top right).
2. Open the **After payment** tab, or the **Confirmation page** section on older layouts.
3. Choose **Don't show confirmation page → Redirect customers to your website**.
4. URL, exactly as written:
   ```
   <RENDER_URL>/paid?session_id={CHECKOUT_SESSION_ID}
   ```
   Keep `{CHECKOUT_SESSION_ID}` literally. Stripe swaps in the real `cs_live_…` id.
5. **Save / Update link**. The buy.stripe.com URL doesn't change.
6. While you're on this page, copy the link's **ID**. It starts with `plink_` and shows in the browser address bar
   (`…/payment-links/plink_…`) and in the link details.
   It goes in `AIV_STRIPE_PAYMENT_LINK_ID`. The server rejects any session that wasn't paid through this exact link.

Leave **promotion codes** and **quantity adjustment** off on this link.
The server only accepts a total of exactly 1900 USD cents (`AIV_STRIPE_EXPECTED_AMOUNT` / `_CURRENCY`).
If you turn on discounts, automatic tax added on top of the price, or quantity, tell us and we'll widen the check.

## 2. Restricted API key, read-only

1. Dashboard → **Developers → API keys** → **Create restricted key**.
   If Stripe asks how you'll use the key, pick "Building your own integration".
2. Name: `aiv-render-readonly`.
3. Permissions: set **Checkout Sessions → Read**. Leave **everything else at None**.
   - No other permission is needed. The server makes exactly one call, `GET /v1/checkout/sessions/{id}`.
     Every field it checks is on that object: `payment_status`, `status`, `mode`, `amount_total`, `currency`,
     `payment_link`, `client_reference_id`, `livemode`.
   - No Payment Links read is needed: you supply the `plink_` ID in step 1.6.
   - No Charges, Customers or Payment Intents access, and no write access of any kind.
   - If you already made a key that also has **read-only** Charges or Balance transactions, it works too; the extra permissions are simply unused. Never use a full `sk_` secret key: the server refuses anything that isn't `rk_`.
4. **Create key**. Copy the `rk_live_…` value. Stripe shows it only once.
5. Put it straight into the Render service's environment, not into chat or email:
   ```
   STRIPE_RESTRICTED_KEY=rk_live_…
   AIV_STRIPE_PAYMENT_LINK_ID=plink_…
   ```
   The server refuses a full secret key (`sk_…`) on purpose.
   With no `rk_` key or no `plink_` ID, nothing unlocks: `/paid` shows "payment verification not switched on" and the
   paywall stays locked.

## 3. (Optional, recommended) Webhook backup route

This covers buyers who close the tab before the redirect lands.
It is also needed for delayed payment methods, which can be "unpaid" at redirect time.

1. Dashboard → **Developers → Webhooks**, or **Workbench → Webhooks / Event destinations** → **Add destination** or **Add endpoint**.
2. Events: `checkout.session.completed`. Also add `checkout.session.async_payment_succeeded`, which is cheap insurance for delayed methods.
   Payload style: snapshot is fine.
3. Endpoint URL: `<RENDER_URL>/stripe/webhook`
4. Create it. Then open the endpoint and **Reveal** the **Signing secret** (`whsec_…`).
5. Put it in Render's environment: `STRIPE_WEBHOOK_SECRET=whsec_…`.
   Tell us when it's set. We only need to know that it exists, not its value.

Even a correctly signed event isn't trusted by itself.
The server re-fetches the session with the restricted key and records it in the same used-session ledger
(`data/stripe/used_sessions.jsonl`).
So whichever route arrives first, redirect or webhook, unlocks the audit, and the other one is a no-op.

## 4. Check it

- Dashboard → Payment Links → **Test link**, or do a real $19 purchase and refund it.
  Use a URL like `https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02?client_reference_id=<an audit id from a free check>`.
- After paying, you land on `<RENDER_URL>/paid?...`. It immediately redirects to `<RENDER_URL>/order/<audit_id>`,
  which shows "generating" and refreshes every 10 s until the full report and fix pack links appear.
- In the session in the Dashboard, check that **client_reference_id** equals the audit id.

## What the server verifies on `/paid?session_id=…`

| Check | Value |
|---|---|
| `session_id` format | `cs_(live\|test)_…`, else no API call is made |
| Retrieved with | `STRIPE_RESTRICTED_KEY` (`rk_…`), `GET api.stripe.com/v1/checkout/sessions/{id}` |
| `status` / `mode` / `payment_status` | `complete` / `payment` / `paid` |
| `payment_link` | `== AIV_STRIPE_PAYMENT_LINK_ID` |
| amount + currency | `amount_total == 1900` and `currency == usd` (integration currency, see Adaptive Pricing below) |
| `client_reference_id` | a well-formed audit id that still exists (audits are deleted after 7 days) |
| single use | session id not already in `data/stripe/used_sessions.jsonl` |

## Adaptive Pricing notes

- Since API version 2025-03-31, a Checkout Session's `currency` / `amount_total` stay in the **integration currency**
  (`usd` / `1900`), even when the buyer pays in CNY or another currency.
  The buyer's local amount is in `presentment_details.presentment_amount` / `presentment_currency`.
  Source: <https://docs.stripe.com/changelog/basil/2025-03-31/add_presentment_details>
- On older account API versions, `currency` / `amount_total` showed the **local** currency, and the USD values were under
  `currency_conversion.source_currency` / `.amount_total`.
  The server handles both shapes, preferring `currency_conversion` when it is present.
  Source: <https://docs.stripe.com/changelog/clover/2025-09-30/remove-checkout-session-currency-conversion-field>
- The CNY price the buyer sees is Stripe's converted amount at checkout, guaranteed for 24 h. It **won't be exactly ¥129**.
  That's why the landing page and report now say "$19, payable in your local currency", not "$19 / ¥129".
- Refunds and the FX cost of Adaptive Pricing: Stripe builds its conversion cost into the buyer's local price, and refunds go back to the buyer's payment method.
  Confirm the current terms under **Settings → Payments → Adaptive Pricing** before launch; we haven't verified them against your account.
