"""Central configuration. Everything is read from environment variables so the
same code runs locally, in Docker, and on a hosted free tier."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SAMPLE_GUIDELINES_ID = "sample-guidelines"
SAMPLE_FACTS_ID = "sample-facts"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # LLM: "gemini" (default), "openai_compat" (OpenAI, Groq, OpenRouter...), or "none"
    llm_provider: str
    gemini_api_key: str | None
    gemini_model: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str | None
    llm_timeout_s: float
    llm_max_retries: int

    # Retrieval: "chroma" or "tfidf" (pure-python fallback, no downloads)
    retriever: str
    cache_dir: Path

    cors_origins: list[str]
    max_draft_chars: int
    max_upload_bytes: int
    max_docs_per_workspace: int
    max_workspaces: int
    max_claim_sentences: int
    llm_audits_per_10min: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    origins = _env("CORS_ORIGINS", "*") or "*"
    return Settings(
        llm_provider=(_env("LLM_PROVIDER", "gemini") or "gemini").lower(),
        gemini_api_key=_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"),
        # Model names change often. Copy the current Flash model id from
        # Google AI Studio into GEMINI_MODEL if this alias stops working.
        gemini_model=_env("GEMINI_MODEL", "gemini-flash-latest") or "gemini-flash-latest",
        llm_base_url=_env("LLM_BASE_URL"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL"),
        llm_timeout_s=float(_env("LLM_TIMEOUT_S", "40") or 40),
        llm_max_retries=int(_env("LLM_MAX_RETRIES", "3") or 3),
        retriever=(_env("RETRIEVER", "chroma") or "chroma").lower(),
        cache_dir=Path(_env("CACHE_DIR", str(BASE_DIR / "llm_cache")) or ""),
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        max_draft_chars=int(_env("MAX_DRAFT_CHARS", "6000") or 6000),
        max_upload_bytes=int(_env("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)) or 0),
        max_docs_per_workspace=int(_env("MAX_DOCS_PER_WORKSPACE", "8") or 8),
        max_workspaces=int(_env("MAX_WORKSPACES", "40") or 40),
        max_claim_sentences=int(_env("MAX_CLAIM_SENTENCES", "24") or 24),
        llm_audits_per_10min=int(_env("LLM_AUDITS_PER_10MIN", "30") or 30),
    )
