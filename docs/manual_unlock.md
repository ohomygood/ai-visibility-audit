# 手动解锁 / Manual unlock (WeChat Pay and other off-Stripe payments)

For the cashier. Chinese buyers pay the owner's personal WeChat QR, ¥129, or ¥99 when the early-bird toggle is on.
They then send the **报告 ID / audit id** (e.g. `20261022-121500-abc123`) plus a payment screenshot or transaction number to the contact.
The contact is **go_with_prob@mail.grokbot.com**, set in `landing/config.js` → `WECHAT_PAY.CONTACT` and env `AIV_WECHAT_CONTACT`. Never publish a personal WeChat ID.
Buyer copy (zh): "付款后把付款截图 + audit id 发到 go_with_prob@mail.grokbot.com，24 小时内开通". **Unlock within 24 h.**
**No WeChat API is ever called**: the cashier checks the payment in WeChat by hand.

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

1. Confirm the money arrived in WeChat (amount and note).
2. Run one of:

**curl (production, from your laptop):**
```bash
read -rs ADMIN_TOKEN    # paste the token; nothing is echoed and it isn't saved to shell history
curl -sS -X POST "<RENDER_URL>/admin/unlock" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"audit_id":"20261022-121500-abc123","by":"owner","reason":"WeChat Pay confirmed",
       "channel":"wechat","amount":129,"currency":"CNY","ref":"4200001234202610220000"}'
```

**CLI, remote (same endpoint, reads `$ADMIN_TOKEN` from env):**
```bash
python3 -m aiv admin-unlock 20261022-121500-abc123 --by owner --reason "WeChat Pay confirmed" \
  --channel wechat --amount 129 --currency CNY --ref 4200001234202610220000 --remote <RENDER_URL>
```

**CLI, local (on the machine that holds `data/`; no token needed, since shell access is the authorization):**
```bash
python3 -m aiv admin-unlock 20261022-121500-abc123 --by owner --reason "WeChat Pay confirmed" \
  --amount 129 --ref 4200001234202610220000
```
The local CLI generates the full report before it exits, which can take a few minutes.
Render's free plan has no shell, so use curl or `--remote` in production.

Field rules:
- `by` and `reason` are required (one line, at most 200 chars).
- `channel` is one of `wechat|alipay|bank|stripe|comp|other` (CLI default `wechat`).
- `currency` defaults to CNY for wechat/alipay.
- `ref` (transaction id, or the note if there is no id) is **required** for wechat/alipay/bank.

3. Responses:
   - `{"result":"unlocked","order_url":"/order/<id>"}`: send the buyer `<RENDER_URL>/order/<id>`. It shows "generating", then the report and fix pack links.
   - `already_unlocked`: no-op, nothing re-runs, but it is still logged.
   - `404 audit not found`: wrong id, or the audit has expired (7 days). Refund, or have the buyer run a new free check and unlock that one.

This uses the same unlocked state (`paid.json`) and the same full-report trigger as Stripe's `/paid`.
An audit that is paid twice, by WeChat and by card, still generates once. Refund the duplicate.

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

- **Chinese report paywall:** "银行卡支付 $19（Stripe）" plus a WeChat block: the QR, ¥129, the audit id, and "付款后把付款截图 + audit id 发到 go_with_prob@mail.grokbot.com，24 小时内开通".
- **Chinese landing page:** the same block. It fills in the audit id after the free check.
- **Early-bird:** set `WECHAT_PAY.EARLYBIRD: true` (landing) and `AIV_WECHAT_EARLYBIRD=1` (reports) to show ¥99 (原价 ¥129). It is off by default.
- **QR image:** `landing/wechat_pay_qr.jpg` is a **local, gitignored copy**. Create it with `scripts/install_wechat_qr.sh`, which copies `/workspace/payment/wechat_pay_qr.jpg`.
  It is referenced by `WECHAT_PAY.QR_URL` and `AIV_WECHAT_QR_URL` (default `/wechat_pay_qr.jpg`).
  It is not in git. Upload it next to the landing page by hand when deploying. If it's missing, the image simply hides.
