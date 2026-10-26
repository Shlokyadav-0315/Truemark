"""Single place that talks to an LLM.

Swap providers by environment variable only:
  LLM_PROVIDER=gemini         + GEMINI_API_KEY (+ GEMINI_MODEL)           <- default, free tier
  LLM_PROVIDER=openai_compat  + LLM_BASE_URL + LLM_API_KEY + LLM_MODEL    <- OpenAI, Groq, OpenRouter...
  LLM_PROVIDER=none                                                       <- rules + numeric checks only

Every response is cached on disk by prompt hash. Commit the cache for your demo drafts and
judges get instant, rate-limit-proof results on the main path."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Protocol

from .config import Settings

log = logging.getLogger("truemark.llm")


class LLMUnavailable(Exception):
    """The LLM cannot be used right now (no key, rate limited, timeout, bad output)."""


class LLM(Protocol):
    name: str
    available: bool

    async def generate_json(self, system: str, prompt: str) -> Any: ...


def parse_json_loose(text: str) -> Any:
    """Parse JSON that may be wrapped in markdown fences or surrounded by prose."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        raise LLMUnavailable("Model did not return JSON.")
    start = min(starts)
    end = max(t.rfind("}"), t.rfind("]"))
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailable("Model returned malformed JSON.") from exc


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(code, int) and code in (408, 429, 500, 502, 503, 504):
        return True
    msg = str(exc).lower()
    return any(s in msg for s in ("429", "resource_exhausted", "unavailable", "overloaded", "timeout", "timed out", "503"))


async def _with_retries(fn, retries: int, timeout_s: float):
    delay = 1.5
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await asyncio.wait_for(fn(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            last = exc
        except LLMUnavailable:
            raise
        except Exception as exc:  # provider SDK errors differ; classify by message/status
            last = exc
            if not _is_retryable(exc):
                raise LLMUnavailable(f"LLM request failed: {type(exc).__name__}") from exc
        if attempt < retries:
            await asyncio.sleep(delay + random.random() * 0.5)
            delay *= 2
    raise LLMUnavailable("The model is busy or rate limited. Try again in a minute.") from last


class NullClient:
    name = "none"
    available = False

    def __init__(self, reason: str = "No LLM API key configured.") -> None:
        self.reason = reason

    async def generate_json(self, system: str, prompt: str) -> Any:
        raise LLMUnavailable(self.reason)


class GeminiClient:
    name = "gemini"
    available = True

    def __init__(self, api_key: str, model: str, retries: int, timeout_s: float) -> None:
        from google import genai  # lazy import so the app boots without the SDK
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self._retries = retries
        self._timeout = timeout_s

    async def generate_json(self, system: str, prompt: str) -> Any:
        async def call() -> str:
            resp = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=self._types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            return resp.text or ""

        text = await _with_retries(call, self._retries, self._timeout)
        return parse_json_loose(text)


class OpenAICompatClient:
    name = "openai_compat"
    available = True

    def __init__(self, base_url: str, api_key: str, model: str, retries: int, timeout_s: float) -> None:
        import httpx

        self._httpx = httpx
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.model = model
        self._retries = retries
        self._timeout = timeout_s

    async def generate_json(self, system: str, prompt: str) -> Any:
        async def call() -> str:
            async with self._httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={
                        "model": self.model,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""

        text = await _with_retries(call, self._retries, self._timeout)
        return parse_json_loose(text)


class CachedLLM:
    """Wraps any client with an on-disk cache keyed by (model, system, prompt)."""

    def __init__(self, inner: LLM, cache_dir: Path) -> None:
        self._inner = inner
        self._dir = cache_dir
        self.name = inner.name
        self.available = True  # cache hits work even when the inner client is unavailable
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _path(self, system: str, prompt: str) -> Path:
        model = getattr(self._inner, "model", "")
        key = hashlib.sha256(f"{self._inner.name}|{model}|{system}|{prompt}".encode()).hexdigest()
        return self._dir / f"{key}.json"

    async def generate_json(self, system: str, prompt: str) -> Any:
        path = self._path(system, prompt)
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        result = await self._inner.generate_json(system, prompt)
        try:
            path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return result

    @property
    def live(self) -> bool:
        """True when a real provider is configured (cache-only mode otherwise)."""
        return getattr(self._inner, "available", False)


def build_llm(settings: Settings) -> CachedLLM:
    inner: LLM
    provider = settings.llm_provider
    try:
        if provider == "gemini" and settings.gemini_api_key:
            inner = GeminiClient(
                settings.gemini_api_key, settings.gemini_model, settings.llm_max_retries, settings.llm_timeout_s
            )
        elif provider == "openai_compat" and settings.llm_base_url and settings.llm_api_key and settings.llm_model:
            inner = OpenAICompatClient(
                settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                settings.llm_max_retries, settings.llm_timeout_s,
            )
        elif provider == "none":
            inner = NullClient("LLM disabled by configuration.")
        else:
            inner = NullClient("No LLM API key configured.")
    except Exception as exc:  # SDK missing or misconfigured: degrade, do not crash the server
        log.warning("LLM init failed: %s", exc)
        inner = NullClient(f"LLM could not start: {type(exc).__name__}")
    return CachedLLM(inner, settings.cache_dir)
