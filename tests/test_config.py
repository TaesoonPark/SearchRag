import os
import unittest
from unittest.mock import patch

from config import load_config


class ConfigLoadTest(unittest.TestCase):
    def test_telegram_open_access_defaults_false(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "dummy",
                "TELEGRAM_ALLOWED_CHAT_IDS": "",
                "TELEGRAM_OPEN_ACCESS": "",
            },
            clear=False,
        ):
            cfg = load_config()
            self.assertEqual(cfg.telegram_allowed_chat_ids, [])
            self.assertFalse(cfg.telegram_open_access)

    def test_telegram_open_access_true_and_chat_ids_parsed(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_TOKEN": "dummy",
                "TELEGRAM_ALLOWED_CHAT_IDS": "123, abc, 456",
                "TELEGRAM_OPEN_ACCESS": "true",
            },
            clear=False,
        ):
            cfg = load_config()
            self.assertEqual(cfg.telegram_allowed_chat_ids, [123, 456])
            self.assertTrue(cfg.telegram_open_access)


if __name__ == "__main__":
    unittest.main()
