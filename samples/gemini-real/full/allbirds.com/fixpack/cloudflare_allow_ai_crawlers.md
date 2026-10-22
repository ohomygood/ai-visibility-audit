# 在 Cloudflare 放行 AI 爬虫 / Allow AI crawlers in Cloudflare

> 菜单名称以 Cloudflare 当前控制台为准（2026 年版）。Menu names follow the current Cloudflare dashboard and may move.

**先确认 / First check:** Shopify 店铺本身就跑在 Shopify 自己的 Cloudflare 上，你无法修改那一层。只有当你把自己的域名
接入了**你自己的** Cloudflare 账号（橙色云朵代理）时，下面的设置才适用。如果你没有自己的 Cloudflare，403 通常来自
防爬/防盗 App（例如 bot-blocker、地区封锁类 App），请先停用这些 App 再复测。
If your store is on Shopify, Shopify's own Cloudflare layer is not configurable by you. These steps apply only when your
domain is proxied through **your own** Cloudflare account. Otherwise look for bot-blocking / geo-blocking apps.

## 1. 关闭 "Block AI bots" / Turn off "Block AI bots"
Security → Settings（或 AI Crawl Control）→ *Block AI bots* → **Do not block (allow crawlers)**。
在 AI Crawl Control 里，至少把 OAI-SearchBot、ChatGPT-User、PerplexityBot、Claude-SearchBot 设为 **Allow**。

## 2. Bot Fight Mode（免费版 / Free plan）
Security → Bots → **Bot Fight Mode: Off**。免费版的 Bot Fight Mode 无法用 WAF 规则绕过，只能关闭。
Free-plan Bot Fight Mode cannot be bypassed with WAF skip rules; it has to be turned off.

## 3. Super Bot Fight Mode（Pro 及以上 / Pro+）
Security → Bots → Configure：
- Verified bots → **Allow**
- Definitely automated → **Allow** 或 **Managed Challenge**（不要选 Block）

## 4. WAF 自定义规则放行已验证的 AI 爬虫 / WAF skip rule
Security → WAF → Custom rules → Create rule:
- Expression:
  ```
  (cf.client.bot) or (cf.verified_bot_category in {"AI Crawler" "AI Search" "AI Assistant"})
  ```
- Action: **Skip** → 勾选 *All remaining custom rules*、*Super Bot Fight Mode*、*Rate limiting rules*
- 把这条规则拖到最上面 / Place it first.

## 5. 复测 / Re-test
Security → Events 里筛选 User agent 包含 `GPTBot`、`OAI-SearchBot`、`PerplexityBot`，确认没有新的 Block/Challenge。
然后重新运行本体检。Re-run this audit afterwards.
