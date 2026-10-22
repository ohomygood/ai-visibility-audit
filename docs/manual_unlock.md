# 手动解锁 / Manual unlock (comps and support)

Stripe is the only payment method. WeChat Pay was removed from all pages on 2026-10-22, and no QR or ¥ price is shown.
This path is for the owner only: free comps, goodwill re-runs, and support cases (e.g. a Stripe payment that
couldn't be linked to an audit). It never takes money itself. Every unlock is logged.

## 1. One-time setup: ADMIN_TOKEN

```bash
openssl rand -hex 32        # 64 hex chars; the server requires at least 32
```

- Store it **only** as a secret env var on the backend: Render → service → Environment → `ADMIN_TOKEN`.
  Keep a copy in your password manager. **Never paste it in chat, e-mail, tickets or the repo.**
- While `ADMIN_TOKEN` is unset or shorter than 32 chars, `POST /admin/unlock` returns 404 (disabled).
- Behind Render or Cloudflare, also set `AIV_TRUST_PROXY=1` so lockouts apply to the real client IP.
  Don't set it when the server is reached directly, because the forwarded headers could be spoofed.
- Rotate: generate a new token, update the Render env var, and redeploy. The old token stops working immediately.

## 2. Unlock a paid audit

1. Confirm why the unlock is justified (comp, or the Stripe payment in the dashboard).
2. Run one of:

**curl (production, from your laptop):**
```bash
read -rs ADMIN_TOKEN    # paste the token; nothing is echoed and it isn't saved to shell history
curl -sS -X POST "<RENDER_URL>/admin/unlock" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"audit_id":"20261022-121500-abc123","by":"owner","reason":"comp for launch partner",
       "channel":"comp","ref":"comp-2026-001"}'
```

**CLI, remote (same endpoint, reads `$ADMIN_TOKEN` from env):**
```bash
python3 -m aiv admin-unlock 20261022-121500-abc123 --by owner --reason "comp for launch partner" \
  --channel comp --ref comp-2026-001 --remote <RENDER_URL>
```

**CLI, local (on the machine that holds `data/`; no token needed, since shell access is the authorization):**
```bash
python3 -m aiv admin-unlock 20261022-121500-abc123 --by owner --reason "comp for launch partner" \
  --amount 129 --ref 4200001234202610220000
```
The local CLI generates the full report before it exits, which can take a few minutes.
Render's free plan has no shell, so use curl or `--remote` in production.

Field rules:
- `by` and `reason` are required (one line, at most 200 chars).
- `channel` is one of `wechat|alipay|bank|stripe|comp|other` (CLI default `comp`).
- `currency` defaults to CNY for wechat/alipay.
- `ref` (transaction id, or the note if there is no id) is **required** for wechat/alipay/bank.

3. Responses:
   - `{"result":"unlocked","order_url":"/order/<id>"}`: send the buyer `<RENDER_URL>/order/<id>`. It shows "generating", then the report and fix pack links.
   - `already_unlocked`: no-op, nothing re-runs, but it is still logged.
   - `404 audit not found`: wrong id, or the audit has expired (7 days). Refund, or have the buyer run a new free check and unlock that one.

This uses the same unlocked state (`paid.json`) and the same full-report trigger as Stripe's `/paid`.
An audit unlocked twice (e.g. a comp and a card payment) still generates once. Refund the duplicate.

### Send the buyer the signed order link

The response includes `order_url` (`/order/<id>?u=…`) and, on Render, a full `order_link`. Send **that** link to the buyer:
it keeps working after a server restart wipes the disk. See docs/recovery.md, including `admin-reapply` and `--url`.

## 3. Security

- The token is compared in constant time and is never logged or echoed.
- 5 failed attempts from one IP in 15 min → that IP gets 429 for 15 min.
- 20 failures in total → the endpoint is locked for everyone for 15 min, even with the right token.
  Failures are logged to `data/admin/security.jsonl`, by IP only.
- `/dev/unlock` works only when `AIV_DEV_UNLOCK=1` **and** `AIV_ENV` is not production.

## 4. Audit log

`data/admin/unlocks.jsonl`, append-only, one JSON line per authorized call, no-ops included:
`ts, audit_id, by, reason, channel, amount, currency, ref, source (endpoint|cli), result`.
The 7-day purge job doesn't touch it: it is a bookkeeping record with no customer e-mail or URL.
Back it up together with `data/stripe/used_sessions.jsonl`. Render's free disk is ephemeral; see docs/deploy.md.

### Durable copy in Render logs

Every admin action is also printed as one stdout line, `ADMIN_AUDIT {json}`. Failed auth attempts are printed too, with the IP only.
These lines survive in Render's log stream even though the free disk is wiped on redeploy. Search the Render logs for `ADMIN_AUDIT`.
`ADMIN_TOKEN` and the Stripe keys are never included; they are scrubbed even if pasted into a field.
Optional: `python3 scripts/export_admin_log.py --out <dir>` copies the log files to dated files. It is **not scheduled**; add a cron job only on a host with a persistent disk.

## 5. The buyer's view

- Pages show only the Stripe price: "$19（银行卡支付，可用本地货币付款）". There is no WeChat block, QR, ¥ price or screenshot instruction.
- After a manual unlock, send the buyer the signed `order_link` from the response (docs/recovery.md).
- **Guard:** `scripts/check_release.py` runs in `scripts/build_static.sh` and fails the build if the site contains any image, a QR/WeChat file name, the owner's real QR (hash), or WeChat/¥ payment copy. `tests/test_admin.py::TestStripeOnlyCopy` checks the landing page, report paywall and order pages.
