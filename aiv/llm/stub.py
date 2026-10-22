"""Deterministic stub engine (default). No network, no cost.

Answers are synthetic and clearly flagged ``simulated=True``; the report shows a
banner so nobody mistakes them for real ChatGPT/Gemini output. Determinism comes
from hashing (engine, prompt, sample index) so tests are stable.
"""
from __future__ import annotations

import hashlib
from .base import LLMResult, Provider

GENERIC_BRANDS = ["Northwind Goods", "Contoso Supply", "Fabrikam", "Tailspin Co", "Adventure Works",
                  "Wingtip", "Litware", "Proseware", "Lucerne", "Margie's Shop"]


class StubProvider(Provider):
    simulated = True
    supports_web_search = True

    def __init__(self, engine: str = "ChatGPT"):
        self.engine = engine
        self.name = f"stub:{engine.lower()}"

    def _complete(self, prompt, *, system="", web_search=True, temperature=0.7, max_tokens=900, sample=0,
                  store_hint=None, **_):
        h = hashlib.sha256(f"{self.engine}|{prompt}|{sample}".encode()).digest()
        store = store_hint
        picks = [GENERIC_BRANDS[(h[i] + i) % len(GENERIC_BRANDS)] for i in range(5)]
        picks = list(dict.fromkeys(picks))[:4]
        if store and h[10] % 3 == 0:  # store is "recommended" in ~1/3 of samples
            picks.insert(h[11] % (len(picks) + 1), store)
        q = prompt.strip().splitlines()[-1][:160]
        lines = [f"(Simulated {self.engine} answer) For \"{q}\", commonly mentioned options are:"]
        lines += [f"{i}. **{b}** – placeholder reason {h[12 + i] % 9 + 1}." for i, b in enumerate(picks, 1)]
        cites = [f"https://example.com/review-{h[20] % 7}", f"https://example.org/best-list-{h[21] % 5}"]
        return LLMResult(text="\n".join(lines), citations=cites if web_search else [], model="stub-v1",
                         input_tokens=len(prompt) // 4, output_tokens=120, simulated=True)
