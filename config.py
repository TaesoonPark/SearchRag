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
    llm_timeout: int

    searxng_base_url: str
    searxng_engines: List[str]
    searxng_enabled: bool
    naver_client_id: str
    naver_client_secret: str
    naver_search_type: str
    naver_search_sort: str
    naver_search_display: int
    naver_search_start: int

    telegram_bot_token: str
    telegram_allowed_chat_ids: List[int]
    run_background: bool

    max_search_attempts: int
    min_docs: int
    min_recent_docs: int
    max_age_days: int
    max_docs_to_fetch: int
    max_doc_chars: int

    fetch_timeout: int
    graph_timeout: int
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

    def _get_bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    cfg = Config(
        llm_base_url=_get("LLM_BASE_URL", "http://localhost:8010/v1"),
        llm_api_key=_get("LLM_API_KEY", "local-token"),
        llm_model=_get("LLM_MODEL", "openai/gpt-oss-120b"),
        llm_timeout=_get_int("LLM_TIMEOUT", 120),
        searxng_base_url=_get("SEARXNG_BASE_URL", "http://localhost:8001"),
        searxng_engines=_get_list("SEARXNG_ENGINES"),
        searxng_enabled=_get_bool("SEARXNG_ENABLED", True),
        naver_client_id=_get("NAVER_CLIENT_ID", ""),
        naver_client_secret=_get("NAVER_CLIENT_SECRET", ""),
        naver_search_type=_get("NAVER_SEARCH_TYPE", "webkr").lower().strip() or "webkr",
        naver_search_sort=_get("NAVER_SEARCH_SORT", "date").lower().strip() or "date",
        naver_search_display=max(1, min(_get_int("NAVER_SEARCH_DISPLAY", 10), 100)),
        naver_search_start=max(1, _get_int("NAVER_SEARCH_START", 1)),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_chat_ids=_get_int_list("TELEGRAM_ALLOWED_CHAT_IDS"),
        run_background=_get_bool("RUN_IN_BACKGROUND", False),
        max_search_attempts=_get_int("MAX_SEARCH_ATTEMPTS", 2),
        min_docs=_get_int("MIN_DOCS", 6),
        min_recent_docs=_get_int("MIN_RECENT_DOCS", 2),
        max_age_days=_get_int("MAX_AGE_DAYS", 30),
        max_docs_to_fetch=_get_int("MAX_DOCS_TO_FETCH", 12),
        max_doc_chars=_get_int("MAX_DOC_CHARS", 2000),
        fetch_timeout=_get_int("FETCH_TIMEOUT", 12),
        graph_timeout=_get_int("GRAPH_TIMEOUT", 600),
        max_fetch_concurrency=_get_int("MAX_FETCH_CONCURRENCY", 6),
        log_level=_get("LOG_LEVEL", "INFO"),
    )

    return cfg
