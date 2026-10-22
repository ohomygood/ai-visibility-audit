# AI 可见度体检 / AI Visibility Audit — Allbirds (allbirds.com)

- Mode: quick · Platform: shopify · 2026-10-22 11:15 (UTC+0800) · ID 20261022-111531-b877fe
- 技术就绪度 / Technical readiness: **80/100 (B)**
- AI mention rate: **80%**
- Engines used: Gemini (gemini-3.5-flash ×3, gemini-3.6-flash ×2, model knowledge only (no live web search: not available on this API tier))
- Crawl 36.1s · total 82.0s · 30 requests · LLM cost $0.0000

## Score

- AI crawler access: 30/30
- robots.txt rules: 15/15
- Structured data (schema): 21/35
- llms.txt: 4/10
- Sitemap: 10/10

## Findings

- [high] /products/mens-cruiser-slip-on-natural-black: JSON-LD block without a schema.org @context: types AggregateRating, Product are not recognised by validators or AI crawlers ×3 → `jsonld/`
- [high] /products/womens-wool-runner-mizzles-natural-black-1: Product is missing required fields: name → `jsonld/`
- [medium] / has no Organization structured data → `jsonld/organization.jsonld`
- [medium] /products/mens-cruiser-slip-on-natural-black has no AggregateRating; AI shopping answers often cite ratings ×3 → `shopify/aiv-product-jsonld.liquid`
- [low] /products/mens-cruiser-slip-on-natural-black: Product could add aggregateRating|review, gtin|gtin8|gtin12|gtin13|gtin14|mpn ×3 → `jsonld/`
- [low] /llms.txt is Shopify's auto-generated generic agent-shopping file, with no store-specific summary, key products or policy links → `llms.txt`

## AI crawlers

- GPTBot: ok (200 200 200 200)
- OAI-SearchBot: ok (200 200 200 200)
- ChatGPT-User: ok (200 200 200 200)
- ClaudeBot: ok (200 200 200 200)
- PerplexityBot: ok (200 200 200 200)
