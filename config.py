from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # Still allow environment variables to be provided externally
        load_dotenv()


@dataclass
class Config:
    llm_base_url: str
    llm_api_key: str
    llm_model: str

    searxng_base_url: str
    searxng_engines: List[str]

    brave_search_api_key: str
    brave_search_country: str
    brave_search_lang: str

    telegram_bot_token: str
    telegram_allowed_chat_ids: List[int]

    max_search_attempts: int
    min_docs: int
    min_recent_docs: int
    max_age_days: int
    max_docs_to_fetch: int
    max_doc_chars: int

    fetch_timeout: int
    max_fetch_concurrency: int
    log_level: str


def load_config() -> Config:
    _load_env()

    def _get(name: str, default: str = "") -> str:
        return os.environ.get(name, default).strip()

    def _get_int(name: str, default: int) -> int:
        value = os.environ.get(name)
        if value is None or not value.strip():
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _get_list(name: str) -> List[str]:
        raw = _get(name)
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _get_int_list(name: str) -> List[int]:
        raw = _get(name)
        if not raw:
            return []
        result: List[int] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.append(int(item))
            except ValueError:
                continue
        return result

    cfg = Config(
        llm_base_url=_get("LLM_BASE_URL", "http://localhost:8000/v1"),
        llm_api_key=_get("LLM_API_KEY", "local-token"),
        llm_model=_get("LLM_MODEL", "gpt-oss-120b"),
        searxng_base_url=_get("SEARXNG_BASE_URL", "http://localhost:8080"),
        searxng_engines=_get_list("SEARXNG_ENGINES"),
        brave_search_api_key=_get("BRAVE_SEARCH_API_KEY", ""),
        brave_search_country=_get("BRAVE_SEARCH_COUNTRY", "US"),
        brave_search_lang=_get("BRAVE_SEARCH_LANG", "en"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_chat_ids=_get_int_list("TELEGRAM_ALLOWED_CHAT_IDS"),
        max_search_attempts=_get_int("MAX_SEARCH_ATTEMPTS", 2),
        min_docs=_get_int("MIN_DOCS", 6),
        min_recent_docs=_get_int("MIN_RECENT_DOCS", 2),
        max_age_days=_get_int("MAX_AGE_DAYS", 30),
        max_docs_to_fetch=_get_int("MAX_DOCS_TO_FETCH", 12),
        max_doc_chars=_get_int("MAX_DOC_CHARS", 2000),
        fetch_timeout=_get_int("FETCH_TIMEOUT", 12),
        max_fetch_concurrency=_get_int("MAX_FETCH_CONCURRENCY", 6),
        log_level=_get("LOG_LEVEL", "INFO"),
    )

    return cfg
