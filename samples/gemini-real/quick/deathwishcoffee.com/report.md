# AI 可见度体检 / AI Visibility Audit — Death Wish Coffee (deathwishcoffee.com)

- Mode: quick · Platform: shopify · 2026-10-22 11:16 (UTC+0800) · ID 20261022-111653-a6bab6
- 技术就绪度 / Technical readiness: **88/100 (A)**
- AI mention rate: **40%**
- Engines used: Gemini (gemini-3.5-flash ×3, gemini-3.5-flash-lite ×2, model knowledge only (no live web search: not available on this API tier))
- Crawl 36.2s · total 81.9s · 30 requests · LLM cost $0.0000

## Score

- AI crawler access: 30/30
- robots.txt rules: 15/15
- Structured data (schema): 29/35
- llms.txt: 4/10
- Sitemap: 10/10

## Findings

- [medium] /products/dark-spirits-chocolate-rumball has no AggregateRating; AI shopping answers often cite ratings ×3 → `shopify/aiv-product-jsonld.liquid`
- [low] /products/dark-spirits-chocolate-rumball: Product could add aggregateRating|review, gtin|gtin8|gtin12|gtin13|gtin14|mpn ×3 → `jsonld/`
- [low] /llms.txt is Shopify's auto-generated generic agent-shopping file, with no store-specific summary, key products or policy links → `llms.txt`

## AI crawlers

- GPTBot: ok (200 200 200 200)
- OAI-SearchBot: ok (200 200 200 200)
- ChatGPT-User: ok (200 200 200 200)
- ClaudeBot: ok (200 200 200 200)
- PerplexityBot: ok (200 200 200 200)
