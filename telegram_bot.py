from __future__ import annotations

import logging
from typing import Callable

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from config import Config

logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LEN = 3500


def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_MESSAGE_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if current_len + len(line) > max_len and current:
            chunks.append("".join(current).rstrip())
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current).rstrip())
    return chunks

def _is_allowed(cfg: Config, chat_id: int) -> bool:
    if not cfg.telegram_allowed_chat_ids:
        return True
    return chat_id in cfg.telegram_allowed_chat_ids


def build_telegram_app(cfg: Config, graph_runner: Callable[[str], str]):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        if not _is_allowed(cfg, update.effective_chat.id):
            return
        await update.message.reply_text("키워드를 보내면 최신 요약을 제공합니다.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None or update.message is None:
            return
        if not _is_allowed(cfg, update.effective_chat.id):
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        await update.message.reply_text("검색 중... 잠시만 기다려주세요.")

        try:
            response = await graph_runner(text)
        except Exception as exc:
            logger.exception("Graph execution failed")
            await update.message.reply_text(f"처리 중 오류가 발생했습니다: {exc}")
            return
        for chunk in _chunk_text(response):
            await update.message.reply_text(chunk)

    app = ApplicationBuilder().token(cfg.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app
