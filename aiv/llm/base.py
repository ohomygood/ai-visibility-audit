"""Pluggable LLM provider interface.

Every engine (stub, Gemini, OpenAI, Perplexity, any OpenAI-compatible endpoint)
implements ``Provider.complete``. ``run_logged`` wraps it with cost logging and a
per-audit budget guard so a report can never exceed its cost cap.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

LOG_DIR = Path(os.environ.get("AIV_LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))


@dataclass
class LLMResult:
    text: str
    citations: list = field(default_factory=list)   # list of URLs the engine cited
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    simulated: bool = False
    error: str | None = None
    latency_ms: int = 0


def redact(msg: str) -> str:
    """Never let API keys reach logs/reports (requests' HTTPError text includes the full URL)."""
    msg = re.sub(r"([?&](?:key|api_key|apikey|token)=)[^&\s]+", r"\1REDACTED", msg)
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "PERPLEXITY_API_KEY", "AIV_OAI_COMPAT_API_KEY"):
        v = os.environ.get(k)
        if v and len(v) > 8:
            msg = msg.replace(v, "REDACTED")
    return msg


class BudgetExceeded(RuntimeError):
    pass


class Provider:
    """Subclass and implement ``_complete``. ``engine`` is the user-facing label
    (e.g. "ChatGPT", "Gemini", "Perplexity") shown in the report."""
    name = "base"
    engine = "base"
    simulated = False
    supports_web_search = False
    # USD per 1M tokens (input, output) plus per-call surcharge (e.g. web search fee)
    price_in = 0.0
    price_out = 0.0
    price_per_call = 0.0

    def estimate_cost(self, n_calls: int, in_tok: int = 400, out_tok: int = 700) -> float:
        return n_calls * (in_tok * self.price_in / 1e6 + out_tok * self.price_out / 1e6 + self.price_per_call)

    def _complete(self, prompt: str, *, system: str = "", web_search: bool = True,
                  temperature: float = 0.7, max_tokens: int = 900) -> LLMResult:
        raise NotImplementedError

    def complete(self, prompt: str, **kw) -> LLMResult:
        t0 = time.monotonic()
        try:
            r = self._complete(prompt, **kw)
        except Exception as e:  # network / quota / parse errors never crash an audit
            r = LLMResult(text="", model=self.name, error=redact(f"{type(e).__name__}: {e}")[:400])
        r.latency_ms = int((time.monotonic() - t0) * 1000)
        if not r.cost_usd:
            r.cost_usd = (r.input_tokens * self.price_in + r.output_tokens * self.price_out) / 1e6 + \
                         (self.price_per_call if not r.error else 0.0)
        r.simulated = r.simulated or self.simulated
        return r


@dataclass
class CostMeter:
    audit_id: str
    budget_usd: float = 1.10
    spent_usd: float = 0.0
    calls: int = 0
    log_path: Path = field(default_factory=lambda: Path(os.environ.get("AIV_LOG_DIR", LOG_DIR)) / "llm_calls.jsonl")

    def run(self, provider: Provider, prompt: str, purpose: str, **kw) -> LLMResult:
        est = provider.estimate_cost(1)
        if self.spent_usd + est > self.budget_usd:
            raise BudgetExceeded(f"budget ${self.budget_usd:.2f} would be exceeded (spent ${self.spent_usd:.4f})")
        r = provider.complete(prompt, **kw)
        self.spent_usd += r.cost_usd
        self.calls += 1
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "audit_id": self.audit_id, "provider": provider.name,
               "engine": provider.engine, "purpose": purpose, **{k: v for k, v in asdict(r).items() if k != "text"},
               "text_chars": len(r.text)}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return r
