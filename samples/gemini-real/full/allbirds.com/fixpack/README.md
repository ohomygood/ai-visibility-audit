# Allbirds — AI 可见度修复包 / AI Visibility Fix Pack

按顺序操作，每步 5–15 分钟。Apply in this order; each step takes 5–15 minutes.

| # | 文件 / File | 作用 / What it does | 放到哪里 / Where |
|---|---|---|---|
| 1 | `cloudflare_allow_ai_crawlers.md` | 解除 AI 爬虫 403 / unblock AI crawlers | Cloudflare 控制台 (only if you run your own Cloudflare) |
| 2 | `shopify/robots.txt.liquid` 或 `robots.txt.snippet` | robots.txt 放行 AI 爬虫 | Shopify: Themes → Edit code → Templates → robots.txt.liquid；其他平台：网站根目录 robots.txt |
| 3 | `shopify/aiv-product-jsonld.liquid` | 每个商品页输出完整 Product/Offer/Rating 结构化数据 | Snippets，然后在 main-product 里 render |
| 4 | `shopify/aiv-organization-jsonld.liquid` / `jsonld/organization.jsonld` | 首页 Organization 数据 | theme.liquid `<head>` |
| 5 | `jsonld/product-*.jsonld` | 非 Shopify 平台或手动粘贴的单品示例 | 商品页 `<script type="application/ld+json">` |
| 6 | `llms.txt` | 给 AI 助手的店铺简介 | 上传到 https://www.allbirds.com/llms.txt（Shopify：Settings → Files 上传后用 URL 重定向 /llms.txt，或使用 llms.txt App） |
| 7 | `content/*.md` | 5 篇 FAQ / 对比 / 选购指南草稿 | 新建页面或博客；先替换所有 `__占位符__` |

完成后用 https://validator.schema.org/ 和 Google Rich Results Test 验证，然后重新体检。
After applying, validate with validator.schema.org and Google's Rich Results Test, then re-run the audit.
