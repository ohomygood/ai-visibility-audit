"""Engine registry. ``AIV_ENGINES`` selects engines, comma separated.

Default ``stub`` = three deterministic simulated engines (ChatGPT/Gemini/Perplexity labels), dev only.

Engine MODE (``AIV_ENGINE_MODE`` or ``--engine-mode``), used when no explicit engine list is given:
  * ``gemini-only`` (default and fallback): Gemini free tier, $0. The free check ALWAYS uses this.
  * ``full``: Gemini + ChatGPT (if OPENAI_API_KEY) + Perplexity (if PERPLEXITY_API_KEY). Missing keys degrade
    gracefully to whatever is configured, and the report lists the engines used and the ones skipped.
Examples: ``AIV_ENGINES=gemini``  ``AIV_ENGINES=gemini,openai_compat``  ``AIV_ENGINES=openai,perplexity,gemini``.
"""
from __future__ import annotations

import os

from .base import BudgetExceeded, CostMeter, LLMResult, Provider  # noqa: F401
from .stub import StubProvider

AVAILABLE = ["stub", "gemini", "openai_compat", "openai", "perplexity"]


def make(name: str) -> list[Provider]:
    name = name.strip().lower()
    if name == "stub":
        return [StubProvider("ChatGPT"), StubProvider("Gemini"), StubProvider("Perplexity")]
    if name.startswith("stub:"):
        return [StubProvider(name.split(":", 1)[1].title())]
    from . import providers as p
    cls = {"gemini": p.GeminiProvider, "openai_compat": p.OpenAICompatProvider,
           "openai": p.OpenAIProvider, "perplexity": p.PerplexityProvider}.get(name)
    if cls is None:
        raise ValueError(f"unknown engine {name!r}; choose from {AVAILABLE}")
    return [cls()]


class ProductionStubError(RuntimeError):
    pass


def is_production() -> bool:
    return os.environ.get("AIV_ENV", "dev").lower() in ("prod", "production")


ENGINE_MODES = ("gemini-only", "full")


def resolve_mode(mode: str | None) -> tuple[str, list[str]]:
    """Turn an engine mode into an engine spec, given which API keys exist. Returns (spec, notes)."""
    mode = (mode or os.environ.get("AIV_ENGINE_MODE") or "gemini-only").strip().lower()
    if mode not in ENGINE_MODES:
        raise ValueError(f"unknown engine mode {mode!r}; choose from {ENGINE_MODES}")
    has = lambda k: bool(os.environ.get(k, "").strip()) and "PENDING" not in os.environ.get(k, "")  # noqa: E731
    notes: list[str] = []
    if not has("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is required for every real-engine mode")
    if mode == "gemini-only":
        return "gemini", notes
    parts = []
    for env, name, label in (("OPENAI_API_KEY", "openai", "ChatGPT"), ("PERPLEXITY_API_KEY", "perplexity", "Perplexity")):
        if has(env):
            parts.append(name)
        else:
            notes.append(f"{label} skipped: {env} not configured")
    parts.append("gemini")
    if parts == ["gemini"]:
        notes.append("engine mode 'full' fell back to 'gemini-only'")
    return ",".join(parts), notes


def select_engines(spec: str | None = None, mode: str | None = None, purpose: str = "full") -> tuple[list[Provider], dict]:
    """Precedence: explicit ``spec`` > ``AIV_ENGINES`` > engine mode. The free/quick check is pinned to
    gemini-only whenever engines come from a mode. Returns (engines, info) where info is shown in the report."""
    spec = spec or os.environ.get("AIV_ENGINES") or ""
    notes: list[str] = []
    used_mode = None
    if not spec.strip() and (mode or os.environ.get("AIV_ENGINE_MODE") or is_production()):
        used_mode = "gemini-only" if purpose == "quick" else (mode or os.environ.get("AIV_ENGINE_MODE") or "gemini-only")
        spec, notes = resolve_mode(used_mode)
    engines = engines_from_env(spec or None)
    return engines, {"mode": used_mode or "explicit", "spec": spec or "stub", "notes": notes}


def engines_from_env(spec: str | None = None) -> list[Provider]:
    """Dev default: ``stub``. Production (AIV_ENV=production): there is NO default, AIV_ENGINES must name
    real engines and any stub engine is refused, so simulated answers can never reach a paying customer."""
    spec = spec or os.environ.get("AIV_ENGINES") or ("" if is_production() else "stub")
    if is_production() and (not spec.strip() or "stub" in spec.lower()):
        raise ProductionStubError("AIV_ENV=production requires real engines in AIV_ENGINES (stub is disabled)")
    out: list[Provider] = []
    for part in spec.split(","):
        if part.strip():
            out += make(part)
    if is_production() and any(e.simulated for e in out):
        raise ProductionStubError("simulated engine refused in production")
    return out
