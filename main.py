from __future__ import annotations

import asyncio
import logging

from config import load_config
from graph import build_graph
from telegram_bot import build_telegram_app


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    cfg = load_config()
    setup_logging(cfg.log_level)

    if not cfg.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    graph = build_graph(cfg)

    async def run_graph(query: str) -> str:
        state = {
            "raw_query": query,
            "query": query,
            "attempts": 0,
            "next_queries": [],
        }
        result = await asyncio.wait_for(graph.ainvoke(state), timeout=cfg.graph_timeout)
        return result.get("response", "")

    app = build_telegram_app(cfg, run_graph)
    app.run_polling()


if __name__ == "__main__":
    main()
