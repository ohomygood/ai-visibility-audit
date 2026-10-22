# Paid state after a wiped disk (Render free)

Render's free disk is wiped on every restart, redeploy and spin-down. Paid orders survive it without paid storage.

## Card payments (Stripe): automatic

- Buy links carry `client_reference_id=<audit_id>_<base32(store domain)>`. That's at most 200 chars, in Stripe's allowed
  `[A-Za-z0-9_-]` alphabet. If a domain is too long to fit (over 110 chars), only the audit id is sent and the order
  can't be rebuilt.
- `/paid` redirects to `/order/<audit_id>?s=<checkout session id>`. The buyer keeps this link, and the page tells them to bookmark it.
- If `/order` finds no local state, it re-fetches the session from Stripe with the restricted key and re-runs every
  `/paid` check. The session must belong to this audit id, otherwise you get `session_mismatch`.
  It then writes `order.json` (store URL) and `paid.json`, and generates the full report again.
- **No double runs.** Only one generation per audit runs at a time (in-process guard), and none starts once a report
  or an error exists. A failed Stripe check is throttled per session for 30 s.
- **Replays are harmless.** The audit id comes from Stripe's copy of `client_reference_id`, so a replayed or shared
  session can only re-open the same order. The ledger (`data/stripe/used_sessions.jsonl`) still records the first
  redemption, but correctness no longer depends on it.
- **Rebuild window.** Rebuilding is allowed for `AIV_RECOVERY_DAYS` (default 30) after the purchase. After that, an
  old link whose data is gone shows `audit_not_found`. This also bounds regeneration after the 7-day retention purge.
- The regenerated report is a fresh full run on the store's home page (`https://<domain>/`). It is not a byte-for-byte copy.

## Manual payments (WeChat / admin): signed link + log replay

- `POST /admin/unlock` and the CLI return `order_url` (and an absolute `order_link` on Render):
  `/order/<audit_id>?u=<base32 domain>.<HMAC-SHA256 signature>`. **Send this link to the buyer.**
  It rebuilds the unlock on its own after a wipe. A link for another audit id, another store, or with a bad signature gets 403.
- **Signing secret:** `AIV_ORDER_LINK_SECRET` (32+ chars). If it isn't set, it is derived from `ADMIN_TOKEN`.
  Rotating `ADMIN_TOKEN` then invalidates links that were already sent, so set `AIV_ORDER_LINK_SECRET` before rotating.
  Admin links don't expire.
- Each `ADMIN_AUDIT` log line now includes the store `domain`. To re-apply every logged manual unlock (idempotent):
  1. Save the Render log lines to a file (`scripts/export_admin_log.py` or the dashboard).
  2. Run:
     ```bash
     ADMIN_TOKEN=… python3 -m aiv admin-reapply render-log.txt --by owner --remote https://ai-visibility-audit-api.onrender.com
     ```
- If the audit's files are already gone, unlock with the store: `admin-unlock <id> … --url shop.example.com`
  (endpoint field `url`). The result is `rebuilt`.

## Not recoverable

- **Unpaid free checks.** The quick report is gone after a wipe. The buyer can run a new free check.
- **Paid orders whose session carries no domain:** orders paid before this change, and domains over 110 chars.
  Use admin-unlock with `--url`, or refund.
- **Rate limits and admin lockout counters** live in memory and reset.
