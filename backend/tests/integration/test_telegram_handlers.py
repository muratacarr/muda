from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from muda.bot_service import BotService
from muda.persistence import jobs, publishing
from muda.persistence.models import Job
from muda.settings import Settings
from muda.telegram_adapter import TelegramHandlers, build_application
from muda.worker import CancelSignals

OWNER = 42


class FakeMessage:
    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.replies: list[tuple[str, Any]] = []

    async def reply_text(self, text: str, reply_markup: Any = None) -> None:
        self.replies.append((text, reply_markup))


def _settings() -> Settings:
    return Settings(
        preview_secret=SecretStr("p" * 40),
        telegram_allowed_user_id=OWNER,
        telegram_bot_token=SecretStr("123456:ABC-test-token"),
        _env_file=None,
    )


def _handlers(engine: Engine) -> TelegramHandlers:
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)
    return TelegramHandlers(BotService(engine, _settings(), CancelSignals()))


def _update(user_id: int, text: str | None, update_id: int = 1) -> Any:
    msg = FakeMessage(text)
    return SimpleNamespace(
        update_id=update_id,
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        callback_query=None,
    )


async def test_owner_text_creates_job_and_replies(engine: Engine) -> None:
    h = _handlers(engine)
    update = _update(OWNER, "Change the Latte price to 150 TL.")
    await h.text(update, None)  # type: ignore[arg-type]
    assert "received" in update.effective_message.replies[0][0].lower()
    with Session(engine) as s:
        assert len(s.scalars(select(Job)).all()) == 1


async def test_stranger_gets_no_reply_and_no_job(engine: Engine) -> None:
    h = _handlers(engine)
    update = _update(999, "Change the Latte price to 1 TL")
    await h.text(update, None)  # type: ignore[arg-type]
    await h.status(update, None)  # type: ignore[arg-type]
    await h.cancel(update, None)  # type: ignore[arg-type]
    assert update.effective_message.replies == []
    with Session(engine) as s:
        assert s.scalars(select(Job)).all() == []


async def test_redelivered_update_is_silent(engine: Engine) -> None:
    h = _handlers(engine)
    first, again = _update(OWNER, "hello", 7), _update(OWNER, "hello", 7)
    await h.text(first, None)  # type: ignore[arg-type]
    await h.text(again, None)  # type: ignore[arg-type]
    assert len(first.effective_message.replies) == 1
    assert again.effective_message.replies == []


async def test_non_text_message_is_ignored(engine: Engine) -> None:
    h = _handlers(engine)
    update = _update(OWNER, None)
    await h.text(update, None)  # type: ignore[arg-type]
    assert update.effective_message.replies == []


def test_application_registers_handlers_and_requires_owner_and_token(engine: Engine) -> None:
    app = build_application(_settings(), BotService(engine, _settings(), CancelSignals()))
    assert sum(len(v) for v in app.handlers.values()) == 6
    bad = Settings(preview_secret=SecretStr("p" * 40), _env_file=None)
    try:
        build_application(bad, BotService(engine, bad, CancelSignals()))
    except RuntimeError:
        pass
    else:
        raise AssertionError("must refuse to start without token and owner")
