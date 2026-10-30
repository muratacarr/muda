"""Thin python-telegram-bot glue. All decisions live in BotService; this only translates."""

import asyncio
import logging
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy import Engine
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from muda.bot_service import BotService, Reply, approval_buttons, failure_text
from muda.domain.site import diff_content
from muda.persistence import publishing
from muda.persistence.models import utcnow
from muda.preview_token import create_preview_token
from muda.settings import Settings
from muda.worker import JobNotification

logger = logging.getLogger("muda.telegram")
Buttons = tuple[tuple[str, str], ...]


class Sender(Protocol):
    async def send(self, chat_id: int, text: str, buttons: Buttons) -> None: ...


def _markup(buttons: Buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in buttons]]
    )


class PtbSender:
    def __init__(self, bot: Any) -> None:
        self._bot = bot

    async def send(self, chat_id: int, text: str, buttons: Buttons) -> None:
        # No parse_mode: message text is never interpreted as Markdown/HTML.
        await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=_markup(buttons))


class TelegramNotifier:
    """Worker notifications -> messages to the single allowed user."""

    def __init__(self, engine: Engine, settings: Settings, sender: Sender) -> None:
        self._engine = engine
        self._settings = settings
        self._sender = sender

    async def notify(self, n: JobNotification) -> None:
        chat_id = self._settings.telegram_allowed_user_id
        if chat_id is None:
            return
        if n.kind == "awaiting_approval" and n.draft_version_id:
            text = await asyncio.to_thread(self._preview_text, n.job_id, n.draft_version_id)
            await self._sender.send(chat_id, text, approval_buttons(n.job_id, n.draft_version_id))
        elif n.kind == "failed":
            await self._sender.send(chat_id, failure_text(n.error_code), ())
        elif n.kind == "cancelled":
            await self._sender.send(chat_id, "Cancelled. Nothing was published.", ())

    def _preview_text(self, job_id: str, draft_version_id: str) -> str:
        ttl = timedelta(minutes=self._settings.preview_ttl_minutes)
        token = create_preview_token(
            self._settings.preview_secret.get_secret_value(),
            job_id,
            draft_version_id,
            ttl,
            now=utcnow(),
        )
        with Session(self._engine) as s:
            published = publishing.get_published(s)[1]
            draft = publishing.get_version_content(s, draft_version_id)
        changes = "\n".join(
            f"• {c.field}: {c.old} -> {c.new}" for c in diff_content(published, draft)
        )
        base = self._settings.public_base_url.rstrip("/")
        return (
            f"Preview ready (job {job_id[:8]}).\n\nChanges:\n{changes or '• (none)'}\n\n"
            f"See it (valid {self._settings.preview_ttl_minutes} min):\n{base}/preview/{token}\n\n"
            "Nothing is live until you approve."
        )


class TelegramHandlers:
    def __init__(self, service: BotService) -> None:
        self._service = service

    async def _reply(self, update: Update, reply: Reply | None) -> None:
        if reply is not None and update.effective_message is not None:
            await update.effective_message.reply_text(
                reply.text, reply_markup=_markup(reply.buttons)
            )

    async def start(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user:
            await self._reply(update, self._service.start(update.effective_user.id))

    async def status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user:
            await self._reply(
                update, await asyncio.to_thread(self._service.status, update.effective_user.id)
            )

    async def history(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user:
            await self._reply(
                update, await asyncio.to_thread(self._service.history, update.effective_user.id)
            )

    async def cancel(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_user:
            await self._reply(
                update, await asyncio.to_thread(self._service.cancel, update.effective_user.id)
            )

    async def text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if update.effective_user and message and message.text:
            reply = await asyncio.to_thread(
                self._service.handle_text, update.update_id, update.effective_user.id, message.text
            )
            await self._reply(update, reply)

    async def callback(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or update.effective_user is None:
            return
        await query.answer()
        reply = await asyncio.to_thread(
            self._service.handle_callback, update.effective_user.id, query.data or ""
        )
        if reply is None:
            return
        await query.edit_message_reply_markup(reply_markup=None)  # buttons are single-use
        await self._reply(update, reply)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Log the exception type only: PTB errors can embed request URLs containing the bot token.
    logger.error("telegram_handler_error", extra={"error_type": type(context.error).__name__})


def build_application(settings: Settings, service: BotService) -> Application:  # type: ignore[type-arg]
    token, owner = settings.telegram_bot_token, settings.telegram_allowed_user_id
    if token is None or owner is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID are required")
    app = Application.builder().token(token.get_secret_value()).build()
    h = TelegramHandlers(service)
    # Defense in depth: PTB drops other users before our handlers; BotService re-checks.
    only_owner = filters.User(user_id=owner) & filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", h.start, filters=only_owner))
    app.add_handler(CommandHandler("status", h.status, filters=only_owner))
    app.add_handler(CommandHandler("history", h.history, filters=only_owner))
    app.add_handler(CommandHandler("cancel", h.cancel, filters=only_owner))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & only_owner, h.text))
    app.add_handler(CallbackQueryHandler(h.callback))
    app.add_error_handler(_on_error)
    return app
