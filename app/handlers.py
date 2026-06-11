"""Telegram router — the single entry point for incoming messages."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import config, digest, specialized
from .telegram_html import split_message

log = logging.getLogger(__name__)


def _authorized(chat_id: int) -> bool:
    return not config.ALLOWED_CHAT_IDS or chat_id in config.ALLOWED_CHAT_IDS


async def _process(update: Update, loading: str, work: Callable[[], str]) -> None:
    chat_id = update.effective_chat.id
    if not _authorized(chat_id):
        log.warning("rejected unauthorized chat_id=%s", chat_id)
        await update.message.reply_text("Not authorized.")
        return

    placeholder = await update.message.reply_text(loading)
    try:
        result = await asyncio.to_thread(work)
    except Exception:
        log.exception("handler work failed")
        await placeholder.edit_text("⚠️ Something went wrong. Try again.")
        return

    chunks = split_message(result) or ["(empty response)"]
    await _send_html(placeholder.edit_text, chunks[0])
    for extra in chunks[1:]:
        await _send_html(update.message.reply_text, extra)


async def _send_html(sender, text: str) -> Message:
    """Send/edit with HTML; if Telegram rejects the markup, retry as plain text."""
    try:
        return await sender(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except BadRequest:
        log.warning("HTML parse rejected by Telegram; sending plain text")
        return await sender(text, disable_web_page_preview=True)


async def digest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _process(
        update,
        "⏳ Analyzing latest tweets and news, please wait...",
        digest.run_digest,
    )


async def updates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    keyword = " ".join(context.args).strip() if context.args else ""
    if not keyword:
        await update.message.reply_text("Usage: /updates <keyword>")
        return
    await _process(
        update,
        "⏳ Fetching protocol updates...",
        lambda: specialized.run_specialized(keyword, chat_id),
    )


async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    await _process(
        update,
        "⏳ Searching the feed...",
        lambda: specialized.run_specialized(text, chat_id),
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler(["digest", "daily_news"], digest_cmd))
    app.add_handler(CommandHandler("updates", updates_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))
