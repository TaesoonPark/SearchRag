from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from app.config import Config, load_config
from app.graph import build_graph
from app.telegram_bot import build_telegram_app

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / "bot_log"
BACKGROUND_ENV = "SEARCHRAG_BACKGROUND_CHILD"
LOGGER = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
        force=True,
    )


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)

    if not cfg.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if not cfg.telegram_allowed_chat_ids and not cfg.telegram_open_access:
        raise RuntimeError(
            "보안상 TELEGRAM_ALLOWED_CHAT_IDS 또는 TELEGRAM_OPEN_ACCESS=true 설정이 필요합니다."
        )

    graph = build_graph(cfg)

    async def run_graph(query: str) -> str:
        state = {
            "raw_query": query,
            "query": query,
            "attempts": 0,
            "next_queries": [],
        }
        try:
            if cfg.graph_timeout > 0:
                result = await asyncio.wait_for(graph.ainvoke(state), timeout=cfg.graph_timeout)
            else:
                result = await graph.ainvoke(state)
            return result.get("response", "")
        except asyncio.TimeoutError:
            LOGGER.warning("Graph execution timeout (query=%r, graph_timeout=%s)", query, cfg.graph_timeout)
            return "요약 생성이 너무 오래 걸립니다. 잠시 후 다시 시도해주세요."

    app = build_telegram_app(cfg, run_graph)
    app.run_polling()


def launch_background(cfg: Config) -> bool:
    if not cfg.run_background or os.environ.get(BACKGROUND_ENV) == "1":
        return False

    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            cwd=PROJECT_ROOT,
            env={**os.environ, BACKGROUND_ENV: "1"},
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    print(f"Started SearchRag bot in background (PID: {process.pid})")
    print(f"Log file: {LOG_PATH}")
    return True


if __name__ == "__main__":
    cfg = load_config()
    if not launch_background(cfg):
        main()
