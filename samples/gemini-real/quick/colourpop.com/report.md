# AI 可见度体检 / AI Visibility Audit — ColourPop (colourpop.com)

- Mode: quick · Platform: shopify · 2026-10-22 11:32 (UTC+0800) · ID 20261022-113231-98de44
- 技术就绪度 / Technical readiness: **88/100 (A)**
- AI mention rate: **40%**
- Engines used: Gemini (gemini-3.5-flash-lite ×5, model knowledge only (no live web search: not available on this API tier))
- Crawl 36.2s · total 36.2s · 30 requests · LLM cost $0.0000

## Score

- AI crawler access: 30/30
- robots.txt rules: 15/15
- Structured data (schema): 29/35
- llms.txt: 4/10
- Sitemap: 10/10

## Findings

- [medium] /products/magical-skies-shadow-palette-and-brush-set has no AggregateRating; AI shopping answers often cite ratings ×3 → `shopify/aiv-product-jsonld.liquid`
- [low] /products/magical-skies-shadow-palette-and-brush-set: Product could add brand, aggregateRating|review, gtin|gtin8|gtin12|gtin13|gtin14|mpn ×3 → `jsonld/`
- [low] /llms.txt is Shopify's auto-generated generic agent-shopping file, with no store-specific summary, key products or policy links → `llms.txt`

## AI crawlers

- GPTBot: ok (200 200 200 200)
- OAI-SearchBot: ok (200 200 200 200)
- ChatGPT-User: ok (200 200 200 200)
- ClaudeBot: ok (200 200 200 200)
- PerplexityBot: ok (200 200 200 200)
