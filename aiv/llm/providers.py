"""Real engines. Each is optional and only activates when its API key env var is set.

Free-tier friendly:
  * gemini     — GEMINI_API_KEY (Google AI Studio free tier; google_search grounding)
  * openai_compat — any OpenAI-compatible chat endpoint, e.g. Groq or OpenRouter free models
                    (AIV_OAI_COMPAT_BASE_URL, AIV_OAI_COMPAT_API_KEY, AIV_OAI_COMPAT_MODEL). No web search.
Paid (need budget approval):
  * openai     — OPENAI_API_KEY, Responses API + web_search tool
  * perplexity — PERPLEXITY_API_KEY, sonar model (search built in)
Prices are configurable via env (AIV_PRICE_<NAME>_IN / _OUT / _CALL, USD per 1M tokens / per call).
"""
from __future__ import annotations

import os
import re
import threading
import time

import requests

from .base import LLMResult, Provider


def _price(name: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    g = lambda k, d: float(os.environ.get(f"AIV_PRICE_{name.upper()}_{k}", d))  # noqa: E731
    return g("IN", default[0]), g("OUT", default[1]), g("CALL", default[2])


class GeminiProvider(Provider):
    """Gemini via the Developer API.

    Grounding (live Google Search) policy, ``AIV_GEMINI_GROUNDING``:
      * ``auto`` (default): try grounded; if the project has no grounding quota (Gemini 3+ free tier has none,
        see https://ai.google.dev/gemini-api/docs/pricing), fall back to ungrounded answers for the rest of the
        process and say so in the report ("model knowledge, no live search").
      * ``on``: grounded only (a 429 is an error).   * ``off``: never grounded (always $0 on the free tier).
    Free-tier pacing: ``AIV_GEMINI_RPM`` (default 10) shared by all threads, plus retry with backoff on 429/503.

    Models: ``AIV_GEMINI_MODELS`` is an ordered fallback chain. The free tier has a small per-model DAILY cap
    (20 requests/day per model on gemini-3.8-flash for this project, Oct 2026), so when a model returns a
    per-day quota error it is marked exhausted for this process and the next model is used. Every answer records
    the model that produced it, and the report lists the models used.
    """
    name = "gemini"
    engine = "Gemini"
    supports_web_search = True
    _lock = threading.Lock()
    _next_at = 0.0
    grounding_unavailable = False
    exhausted: dict = {}   # model -> unix time until which it is treated as out of daily quota
    DEFAULT_MODELS = "gemini-3.5-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-flash-lite-latest"

    def __init__(self, api_key: str | None = None, model: str | None = None, session=None):
        self.api_key = api_key or os.environ["GEMINI_API_KEY"]
        chain = model or os.environ.get("AIV_GEMINI_MODELS") or os.environ.get("AIV_GEMINI_MODEL") or self.DEFAULT_MODELS
        self.models = [m.strip() for m in chain.split(",") if m.strip()]
        self.model = self.models[0]
        self.models_used: dict = {}
        self.http = session or requests
        self.grounding = os.environ.get("AIV_GEMINI_GROUNDING", "auto").lower()
        self.rpm = float(os.environ.get("AIV_GEMINI_RPM", "10"))
        # free tier = 0; paid-tier list prices can be set via env
        self.price_in, self.price_out, self.price_per_call = _price("gemini", (0.0, 0.0, 0.0))
        self.grounded_calls = 0
        self.ungrounded_calls = 0

    @property
    def mode_note(self) -> str:
        if self.grounded_calls and not self.ungrounded_calls:
            return "live Google Search grounding"
        if self.ungrounded_calls and not self.grounded_calls:
            return "model knowledge only (no live web search: not available on this API tier)"
        if self.grounded_calls and self.ungrounded_calls:
            return f"mixed: {self.grounded_calls} grounded / {self.ungrounded_calls} ungrounded answers"
        return ""

    def _pace(self):
        if self.rpm <= 0:
            return
        with GeminiProvider._lock:
            now = time.monotonic()
            wait = max(0.0, GeminiProvider._next_at - now)
            GeminiProvider._next_at = max(now, GeminiProvider._next_at) + 60.0 / self.rpm
        if wait:
            time.sleep(wait)

    @staticmethod
    def _quota(r) -> tuple[str, float]:
        """(quotaId, retryDelay seconds) from a 429 body."""
        qid, delay = "", 0.0
        try:
            for d in r.json().get("error", {}).get("details", []) or []:
                for v in d.get("violations", []) or []:
                    qid = v.get("quotaId", qid)
                if d.get("retryDelay"):
                    delay = float(str(d["retryDelay"]).rstrip("s") or 0)
        except Exception:
            pass
        return qid, delay

    # Exhausted models / missing grounding are remembered across processes until the quota resets.
    @staticmethod
    def _state_path():
        from pathlib import Path
        return Path(os.environ.get("AIV_LOG_DIR", "logs")) / "gemini_quota_state.json"

    @classmethod
    def _load_state(cls):
        import json
        try:
            st = json.loads(cls._state_path().read_text())
        except Exception:
            return
        now = time.time()
        cls.exhausted.update({m: until for m, until in st.get("exhausted", {}).items() if until > now})
        if st.get("grounding_unavailable_until", 0) > now:
            cls.grounding_unavailable = True

    @classmethod
    def _save_state(cls, model=None, delay=0.0, grounding=False):
        import json
        p = cls._state_path()
        try:
            st = json.loads(p.read_text())
        except Exception:
            st = {}
        if model:
            st.setdefault("exhausted", {})[model] = time.time() + max(delay, 600)
        if grounding:
            st["grounding_unavailable_until"] = time.time() + 6 * 3600
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(st, indent=1))
        except OSError:
            pass

    @classmethod
    def quota_left(cls, models: list[str] | None = None) -> bool:
        """False when every model in the chain is marked out of daily quota (no API call is made)."""
        if not getattr(cls, "_state_loaded", False):
            cls._state_loaded = True
            cls._load_state()
        chain = models or [m.strip() for m in (os.environ.get("AIV_GEMINI_MODELS") or os.environ.get("AIV_GEMINI_MODEL")
                                               or cls.DEFAULT_MODELS).split(",") if m.strip()]
        now = time.time()
        return any(cls.exhausted.get(m, 0) <= now for m in chain)

    def _current_model(self):
        if not getattr(GeminiProvider, "_state_loaded", False):
            GeminiProvider._state_loaded = True
            self._load_state()
        for m in self.models:
            if GeminiProvider.exhausted.get(m, 0) <= time.time():   # expires, so a long-running server recovers after the reset
                return m
        raise RuntimeError("all Gemini models hit their free-tier daily quota: " + ",".join(self.models))

    def _post(self, body):
        delays = [5, 15, 40]
        attempt = 0
        switched = False
        while True:
            model = self._current_model()
            if not switched:
                self._pace()
            switched = False
            r = self.http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                               headers={"x-goog-api-key": self.api_key}, json=body, timeout=90)
            r.model_used = model
            if r.status_code == 429:
                qid, delay = self._quota(r)
                if "PerDay" in qid:
                    GeminiProvider.exhausted[model] = time.time() + max(delay, 600)   # switch model, don't count as a retry or a pacing slot
                    self._save_state(model=model, delay=delay)
                    switched = True
                    continue
                if "tools" in body and self.grounding == "auto":
                    return r  # let the caller decide whether this is "no grounding quota"
                if attempt < len(delays):
                    time.sleep(min(max(delay, delays[attempt]), 60))
                    attempt += 1
                    continue
                return r
            if r.status_code in (500, 503) and attempt < len(delays):
                time.sleep(delays[attempt])
                attempt += 1
                continue
            return r

    def _complete(self, prompt, *, system="", web_search=True, temperature=0.7, max_tokens=900, **_):
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max(max_tokens, 2048),
                                     "thinkingConfig": {"thinkingLevel": os.environ.get("AIV_GEMINI_THINKING", "minimal")}}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        self._current_model()  # loads persisted quota state once
        ground = web_search and self.grounding != "off" and not (self.grounding == "auto" and GeminiProvider.grounding_unavailable)
        if ground:
            body["tools"] = [{"google_search": {}}]
        r = self._post(body)
        if ground and r.status_code == 429 and self.grounding == "auto":
            # Probe once without the tool: if that works, the 429 was the grounding quota, not the model quota.
            body.pop("tools")
            r2 = self._post(body)
            if r2.ok:
                GeminiProvider.grounding_unavailable = True
                self._save_state(grounding=True)
            r, ground = r2, False
        if r.status_code == 400 and "thinking" in r.text.lower():
            body["generationConfig"].pop("thinkingConfig", None)
            r = self._post(body)
        r.raise_for_status()
        d = r.json()
        cand = (d.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
        cites = []
        for c in cand.get("groundingMetadata", {}).get("groundingChunks", []):
            w = c.get("web", {})
            uri, title = w.get("uri", ""), (w.get("title") or "").strip().lower()
            # Grounding URIs are vertexaisearch redirect links; the title carries the source domain.
            if "vertexaisearch" in uri and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", title):
                cites.append(f"https://{title}/")
            elif uri and "vertexaisearch" not in uri:
                cites.append(uri)
        if ground:
            self.grounded_calls += 1
        else:
            self.ungrounded_calls += 1
        u = d.get("usageMetadata", {})
        if not text and cand.get("finishReason") not in (None, "STOP"):
            raise RuntimeError(f"empty answer (finishReason={cand.get('finishReason')})")
        used = getattr(r, "model_used", self.model)
        self.models_used[used] = self.models_used.get(used, 0) + 1
        return LLMResult(text=text, citations=list(dict.fromkeys(cites)), model=d.get("modelVersion") or used,
                         input_tokens=u.get("promptTokenCount", 0),
                         output_tokens=u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0))


class OpenAICompatProvider(Provider):
    name = "openai_compat"
    supports_web_search = False

    def __init__(self, base_url=None, api_key=None, model=None, engine=None, session=None):
        self.base_url = (base_url or os.environ["AIV_OAI_COMPAT_BASE_URL"]).rstrip("/")
        self.api_key = api_key or os.environ["AIV_OAI_COMPAT_API_KEY"]
        self.model = model or os.environ.get("AIV_OAI_COMPAT_MODEL", "llama-3.3-70b-versatile")
        self.engine = engine or os.environ.get("AIV_OAI_COMPAT_LABEL", f"LLM ({self.model})")
        self.http = session or requests
        self.price_in, self.price_out, self.price_per_call = _price("openai_compat", (0.0, 0.0, 0.0))

    def _complete(self, prompt, *, system="", web_search=True, temperature=0.7, max_tokens=900, **_):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        r = self.http.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "messages": msgs, "temperature": temperature,
                                 "max_tokens": max_tokens}, timeout=60)
        r.raise_for_status()
        d = r.json()
        u = d.get("usage", {})
        return LLMResult(text=d["choices"][0]["message"]["content"] or "", model=self.model,
                         citations=d.get("citations", []) or [],
                         input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0))


class PerplexityProvider(OpenAICompatProvider):
    name = "perplexity"
    supports_web_search = True

    def __init__(self, api_key=None, model=None, session=None):
        super().__init__(base_url="https://api.perplexity.ai", api_key=api_key or os.environ["PERPLEXITY_API_KEY"],
                         model=model or os.environ.get("AIV_PERPLEXITY_MODEL", "sonar"), engine="Perplexity",
                         session=session)
        # sonar: $1/$1 per 1M tokens + ~$5 per 1k requests (low search context)
        self.price_in, self.price_out, self.price_per_call = _price("perplexity", (1.0, 1.0, 0.005))


class OpenAIProvider(Provider):
    name = "openai"
    engine = "ChatGPT"
    supports_web_search = True

    def __init__(self, api_key=None, model=None, session=None):
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        self.model = model or os.environ.get("AIV_OPENAI_MODEL", "gpt-4.1-mini")
        self.http = session or requests
        # gpt-4.1-mini list price (2026-10): $0.40/$1.60 per 1M; web_search $10/1k calls + fixed 8,000 search-content
        # input tokens per call ($0.0032) -> per-call surcharge ~= $0.0132
        self.price_in, self.price_out, self.price_per_call = _price("openai", (0.4, 1.6, 0.0132))

    def _complete(self, prompt, *, system="", web_search=True, temperature=0.7, max_tokens=900, **_):
        body = {"model": self.model, "input": prompt, "max_output_tokens": max_tokens}
        if system:
            body["instructions"] = system
        if web_search:
            body["tools"] = [{"type": "web_search"}]
        r = self.http.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {self.api_key}"},
                           json=body, timeout=90)
        r.raise_for_status()
        d = r.json()
        text, cites = "", []
        for item in d.get("output", []):
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                    cites += [a.get("url") for a in c.get("annotations", []) if a.get("url")]
        u = d.get("usage", {})
        return LLMResult(text=text, citations=list(dict.fromkeys(cites)), model=self.model,
                         input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0))
