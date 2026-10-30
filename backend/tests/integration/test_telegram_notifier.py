from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from muda.domain.job_state import JobState
from muda.domain.operations import UpdateMenuPrice, parse_operation
from muda.persistence import jobs, publishing
from muda.persistence.models import utcnow
from muda.preview_token import verify_preview_token
from muda.settings import Settings
from muda.telegram_adapter import TelegramNotifier
from muda.worker import JobNotification

OWNER = 42


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, chat_id: int, text: str, buttons: tuple[tuple[str, str], ...]) -> None:
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})


@pytest.fixture
def settings() -> Settings:
    return Settings(
        preview_secret=SecretStr("p" * 40),
        telegram_allowed_user_id=OWNER,
        public_base_url="https://luna.example.com",
        _env_file=None,
    )


def _awaiting(engine: Engine) -> tuple[str, str]:
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)
        jid = jobs.create_job(s, update_id=1, telegram_user_id=OWNER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
        op = parse_operation(
            {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
        )
        assert isinstance(op, UpdateMenuPrice)
        return jid, publishing.create_draft(s, jid, op)


async def test_awaiting_approval_message_has_preview_link_changes_and_buttons(
    engine: Engine, settings: Settings
) -> None:
    jid, draft = _awaiting(engine)
    sender = FakeSender()
    await TelegramNotifier(engine, settings, sender).notify(
        JobNotification(jid, "awaiting_approval", draft_version_id=draft)
    )
    [msg] = sender.sent
    assert msg["chat_id"] == OWNER
    assert "menu.latte.price: 130 -> 150" in msg["text"]
    assert [b[0] for b in msg["buttons"]] == ["Approve", "Reject"]
    assert all(len(b[1].encode()) <= 64 for b in msg["buttons"])  # Telegram callback_data cap

    link = next(w for w in msg["text"].split() if w.startswith("https://luna.example.com/preview/"))
    token = link.rsplit("/", 1)[1]
    claims = verify_preview_token(settings.preview_secret.get_secret_value(), token, now=utcnow())
    assert claims is not None
    assert (claims.job_id, claims.draft_version_id) == (jid, draft)


async def test_failed_and_cancelled_messages_say_nothing_was_changed(
    engine: Engine, settings: Settings
) -> None:
    sender = FakeSender()
    notifier = TelegramNotifier(engine, settings, sender)
    await notifier.notify(JobNotification("j", "failed", error_code="AI_TIMEOUT"))
    await notifier.notify(JobNotification("j", "failed", error_code="SOMETHING_NEW"))
    await notifier.notify(JobNotification("j", "cancelled"))
    texts = [m["text"] for m in sender.sent]
    assert all("nothing" in t.lower() for t in texts)
    assert all(m["buttons"] == () for m in sender.sent)


async def test_notifier_never_sends_to_anyone_but_the_owner(
    engine: Engine, settings: Settings
) -> None:
    sender = FakeSender()
    await TelegramNotifier(engine, settings, sender).notify(JobNotification("j", "cancelled"))
    assert {m["chat_id"] for m in sender.sent} == {OWNER}


async def test_notifier_without_configured_owner_sends_nothing(engine: Engine) -> None:
    s = Settings(preview_secret=SecretStr("p" * 40), _env_file=None)
    sender = FakeSender()
    await TelegramNotifier(engine, s, sender).notify(JobNotification("j", "cancelled"))
    assert sender.sent == []
