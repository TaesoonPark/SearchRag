import unittest

from config import Config
from telegram_bot import _is_allowed


def _base_config() -> Config:
    return Config(
        llm_base_url="http://localhost:8000/v1",
        llm_api_key="token",
        llm_model="model",
        llm_timeout=30,
        searxng_base_url="http://localhost:8080",
        searxng_engines=[],
        searxng_enabled=True,
        naver_client_id="",
        naver_client_secret="",
        naver_search_type="webkr",
        naver_search_sort="date",
        naver_search_display=10,
        naver_search_start=1,
        google_service_account_path="",
        google_user_email="",
        google_oauth_client_path="",
        google_oauth_token_path="",
        google_calendar_id="primary",
        telegram_bot_token="token",
        telegram_allowed_chat_ids=[],
        telegram_open_access=False,
        run_background=False,
        max_search_attempts=2,
        min_docs=6,
        min_recent_docs=2,
        max_age_days=30,
        max_docs_to_fetch=12,
        max_doc_chars=2000,
        fetch_timeout=12,
        graph_timeout=600,
        max_fetch_concurrency=6,
        log_level="INFO",
    )


class TelegramAccessTest(unittest.TestCase):
    def test_denied_when_allowlist_empty_and_open_access_false(self):
        cfg = _base_config()
        self.assertFalse(_is_allowed(cfg, 111))

    def test_allowed_when_open_access_true(self):
        cfg = _base_config()
        cfg.telegram_open_access = True
        self.assertTrue(_is_allowed(cfg, 111))

    def test_allowlist_has_priority(self):
        cfg = _base_config()
        cfg.telegram_allowed_chat_ids = [111, 222]
        self.assertTrue(_is_allowed(cfg, 111))
        self.assertFalse(_is_allowed(cfg, 999))


if __name__ == "__main__":
    unittest.main()
