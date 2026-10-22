"""Offline cost estimate for one full report at list prices (no API calls, no keys needed).

Usage:
  python3 scripts/estimate_cost.py                 # default sampling: 25 questions, ChatGPT x2, Perplexity x2, Gemini x3
  python3 scripts/estimate_cost.py 25 3            # uniform: 25 questions x 3 samples on every engine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiv.llm.providers import GeminiProvider, OpenAIProvider, PerplexityProvider  # noqa: E402
from aiv.visibility import DEFAULT_FULL_SAMPLES, samples_for  # noqa: E402

q = int(sys.argv[1]) if len(sys.argv) > 1 else 25
samples = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_FULL_SAMPLES
engines = [OpenAIProvider(api_key="x"), PerplexityProvider(api_key="x"), GeminiProvider(api_key="x")]
total = 0.0
print(f"{q} questions, samples per engine: {samples} (+5 content drafts on the first engine, no search)")
for e in engines:
    n = q * samples_for(e, samples)
    c = e.estimate_cost(n, in_tok=150, out_tok=600)
    total += c
    note = " (free tier: $0 but ungrounded, 20 req/day per flash model; paid tier: Gemini 3+ grounding 5,000 searches/month free then $14/1,000)" if e.name == "gemini" else ""
    print(f"  {e.engine:11s} {n:4d} calls  ${c:.3f}{note}")
drafts = 5 * (900 * engines[0].price_in + 1400 * engines[0].price_out) / 1e6
total += drafts
from aiv.llm.base import CostMeter  # noqa: E402
cap = CostMeter("estimate").budget_usd
print(f"  drafts       5 calls  ${drafts:.3f}\n  TOTAL ${total:.3f}  ({'under' if total < 1 else 'OVER'} the $1 target; "
      f"{'within' if total <= cap else 'OVER'} the ${cap:.2f} per-audit cap)")

print("\nengine mode 'gemini-only' (default until paid keys exist): 75 Gemini calls + 5 drafts = 80 calls, $0.000 on the free tier")
print("engine mode 'full' (all three engines): the TOTAL above")
