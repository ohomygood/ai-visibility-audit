# AI 可见度体检 / AI Visibility Audit — Gymshark (gymshark.com)

- Mode: quick · Platform: shopify · 2026-10-22 11:33 (UTC+0800) · ID 20261022-113307-829a30
- 技术就绪度 / Technical readiness: **83/100 (B)**
- AI mention rate: **100%**
- Engines used: Gemini (gemini-3.5-flash-lite ×5, model knowledge only (no live web search: not available on this API tier))
- Crawl 28.2s · total 65.6s · 29 requests · LLM cost $0.0000

## Score

- AI crawler access: 30/30
- robots.txt rules: 15/15
- Structured data (schema): 28/35
- llms.txt: 0/10
- Sitemap: 10/10

## Findings

- [medium] /products/gymshark-gs-x-sam-sulek-zip-through-hoodie-pullovers-black-aw26 has no AggregateRating; AI shopping answers often cite ratings ×3 → `shopify/aiv-product-jsonld.liquid`
- [medium] No /llms.txt: AI assistants have no concise, curated summary of your store → `llms.txt`
- [low] /products/gymshark-gs-x-sam-sulek-zip-through-hoodie-pullovers-black-aw26: Product could add sku, offers, aggregateRating|review, gtin|gtin8|gtin12|gtin13|gtin14|mpn ×3 → `jsonld/`

## AI crawlers

- GPTBot: ok (200 200 200 200)
- OAI-SearchBot: ok (200 200 200 200)
- ChatGPT-User: ok (200 200 200 200)
- ClaudeBot: ok (200 200 200 200)
- PerplexityBot: ok (200 200 200 200)
