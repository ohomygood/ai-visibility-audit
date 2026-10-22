# Real-engine samples — Gemini free tier only ($0), 2026-10-22 (UTC+8)

These are REAL engine outputs (not simulated). Engine: Gemini Developer API, free tier, **no live web search**
(Google Search grounding is not available on the free tier for the Gemini 3.x models this key can use), so answers
reflect model knowledge only and there is no citation data. Every report states this in its "Engines used" line.
Paid engines were never called (`OPENAI_API_KEY`/`PERPLEXITY_API_KEY` unset, `--budget 0`). Spend: **$0.00**.

| Mode | Store | Score | Gemini mention rate | Crawl s | Total s | LLM calls | Model(s) | Top competitors named |
|---|---|---|---|---|---|---|---|---|
| quick | allbirds.com | 80 B | 4/5 | 36 | 82* | 5 | gemini-3.5-flash→3.6-flash | Veja, HOKA, Nordstrom, Rothy's, Thursday Boots |
| quick | deathwishcoffee.com | 88 | 2/5 | 36 | 82* | 5 | 3.5-flash, 3.5-flash-lite | Trade Coffee, Onyx Coffee Lab, La Colombe, Peet's |
| quick | colourpop.com | 88 | 2/5 | 36 | **36** | 5 | 3.5-flash-lite | Sephora, Ulta Beauty, Cult Beauty, Rare Beauty, Fenty |
| quick | gymshark.com | 83 | 5/5 | 28 | 66 | 5 | 3.5-flash-lite | Lululemon, Vuori, Alphalete, Alo Yoga, Nike |
| **full** | allbirds.com | 80 B | 51/75 (68%) | 44 | **513** (8.6 min) | 75 + 5 drafts | 3.5-flash-lite ×75 | Rothy's, Zappos, REI, Nordstrom, On Running, Cariuma |

\* includes one-off probes that discovered the grounding / daily quotas; later runs reuse the persisted quota state.
`archive-run1-before-fixes/` and `archive-run2-wrong-category/` keep the earlier runs that exposed the bugs below.

## Quality assessment
- **Relevance:** good. Answers are on-topic shopping answers with concrete brands and store links.
- **Brands/competitors:** real and plausible for each niche (e.g. Rothy's, Cariuma, Veja, Thursday Boot for Allbirds;
  Alphalete, Vuori, Alo for Gymshark). Retailers (Zappos, REI, Nordstrom, Sephora, Ulta) also appear: they are where
  AI sends shoppers, so we keep them.
- **Citations:** none (no grounding). Links inside answers are brand homepages; the report now labels that section
  "Websites linked in AI answers" instead of "Sources AI cites" when no engine searched the web.
- **Mention rate:** inflated by the 2 of 5 quick questions that name the store ("Is X legit?", "alternatives to X");
  category questions are the meaningful signal. Full report per-question table shows this split.

## Bugs found with real answers and fixed
1. Default model `gemini-2.5-flash` → 404 for new users. Now a fallback chain (`AIV_GEMINI_MODELS`, default
   3.5-flash, 3.6-flash, 3.5-flash-lite, flash-lite-latest) that switches model on a per-day quota error.
2. Search grounding 429 on free tier → `AIV_GEMINI_GROUNDING=auto` falls back to ungrounded answers and the report says so.
3. Grounding citations are `vertexaisearch` redirect URLs (were all discarded) → now mapped to the source domain from the chunk title.
4. Thinking parts / thinking tokens: thought parts excluded from answer text; thinking level `minimal` (6 s/call instead of 45 s+).
5. Free-tier pacing: shared 10 RPM limiter, 429/503 backoff; exhausted models and missing grounding persisted in `logs/gemini_quota_state.json`.
6. Brand parser counted markdown links (`[Cuyana](https://…)`), section headings ("Why it's great", "Tip…", "The Pros…") and quoted terms as brands; `Rothy's`/`Rothy’s` counted twice; own brand ("Gymshark Official Store") counted as competitor → fixed + regression tests.
7. Wrong category in questions (Gymshark → "accessories", ColourPop → "peel off lip stain") → category now picked from the store's own wording with a shopper-level vocabulary, product types as fallback.
8. Quick check ran the LLM after the crawl → now in parallel (colourpop 36 s end to end).

## Free-tier capacity (measured)
- `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.5-flash`/`3.6-flash`: **20 requests/day per model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, quota value 20). Resets midnight Pacific (15:00 UTC+8).
- `gemini-3.5-flash-lite`: ≥100 requests today without hitting a cap (limit not disclosed).
- One full report = 80 calls, one quick check = 5. So the free tier supports dozens of quick checks/day but only about one full report/day per project. **Selling reports at volume needs Gemini's paid tier** (billing account) or the paid ChatGPT/Perplexity engines.
