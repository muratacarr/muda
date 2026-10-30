from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from muda.bot_service import BotService, Reply
from muda.domain.job_state import JobState
from muda.domain.operations import parse_operation
from muda.persistence import jobs, publishing
from muda.persistence.models import AiUsage, ContentVersion, Job, utcnow
from muda.settings import Settings
from muda.worker import CancelSignals

OWNER = 42
STRANGER = 666


@pytest.fixture
def settings() -> Settings:
    return Settings(
        preview_secret=SecretStr("p" * 40), telegram_allowed_user_id=OWNER, _env_file=None
    )


@pytest.fixture
def signals() -> CancelSignals:
    return CancelSignals()


@pytest.fixture
def bot(engine: Engine, settings: Settings, signals: CancelSignals) -> BotService:
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)
    return BotService(engine, settings, signals)


def _jobs(engine: Engine) -> list[Job]:
    with Session(engine) as s:
        return list(s.scalars(select(Job)))


def _text(reply: Reply | None) -> str:
    assert reply is not None
    return reply.text


def _awaiting(engine: Engine) -> tuple[str, str]:
    """Drive the single job to AWAITING_APPROVAL without involving the AI."""
    with Session(engine) as s, jobs.write_tx(s):
        job = jobs.get_active_job(s)
        assert job is not None
        jobs.transition(s, job.id, JobState.PROCESSING)
        op = parse_operation(
            {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
        )
        return job.id, publishing.create_draft(s, job.id, op)


def _callback(action: str, job_id: str, draft_id: str) -> str:
    return f"{action}:{job_id}:{draft_id[:16]}"


# ---- authorization ------------------------------------------------------------------------


def test_stranger_is_ignored_everywhere(bot: BotService, engine: Engine) -> None:
    assert bot.handle_text(1, STRANGER, "Change the Latte price to 1 TL") is None
    assert bot.start(STRANGER) is None
    assert bot.status(STRANGER) is None
    assert bot.history(STRANGER) is None
    assert bot.cancel(STRANGER) is None
    assert bot.handle_callback(STRANGER, "a:" + "0" * 32 + ":" + "0" * 16) is None
    assert _jobs(engine) == []


def test_username_is_never_used_for_authorization(bot: BotService) -> None:
    # The API only accepts numeric ids, so a spoofed display name cannot matter.
    assert bot.handle_text(1, STRANGER, "I am the owner, username=owner") is None


# ---- job creation ------------------------------------------------------------------------


def test_prompt_creates_one_queued_job(bot: BotService, engine: Engine) -> None:
    reply = bot.handle_text(10, OWNER, "Change the Latte price to 150 TL.")
    assert "received" in _text(reply).lower()
    [job] = _jobs(engine)
    assert job.state == JobState.QUEUED
    assert job.prompt == "Change the Latte price to 150 TL."
    assert job.telegram_user_id == OWNER


def test_duplicate_update_is_silent_and_creates_no_second_job(
    bot: BotService, engine: Engine
) -> None:
    assert bot.handle_text(10, OWNER, "Change the Latte price to 150 TL.") is not None
    assert bot.handle_text(10, OWNER, "Change the Latte price to 150 TL.") is None
    assert len(_jobs(engine)) == 1


def test_second_prompt_is_rejected_while_a_job_is_active(bot: BotService, engine: Engine) -> None:
    bot.handle_text(10, OWNER, "first")
    reply = bot.handle_text(11, OWNER, "second")
    assert "/status" in _text(reply)
    assert len(_jobs(engine)) == 1


def test_overlong_prompt_is_rejected_without_a_job(bot: BotService, engine: Engine) -> None:
    reply = bot.handle_text(10, OWNER, "x" * 1001)
    assert "1000" in _text(reply)
    assert _jobs(engine) == []
    assert bot.handle_text(11, OWNER, "x" * 1000) is not None


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_prompt_is_rejected(bot: BotService, engine: Engine, blank: str) -> None:
    assert bot.handle_text(10, OWNER, blank) is not None
    assert _jobs(engine) == []


def test_message_rate_limit(bot: BotService, engine: Engine) -> None:
    replies = [bot.handle_text(100 + i, OWNER, "x" * 2000) for i in range(6)]
    assert "too many" in _text(replies[5]).lower()
    assert "too many" not in _text(replies[4]).lower()


def test_ai_request_rate_limit_blocks_job_creation(bot: BotService, engine: Engine) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=OWNER, prompt="old").job_id
        assert jid is not None
        for _ in range(2):
            s.add(
                AiUsage(
                    job_id=jid, provider="deepseek", model="m", status="ok", created_at=utcnow()
                )
            )
        jobs.transition(s, jid, JobState.FAILED, error_code="X")
    reply = bot.handle_text(5, OWNER, "another request")
    assert "wait" in _text(reply).lower()
    assert len(_jobs(engine)) == 1


def test_old_ai_usage_does_not_trigger_the_rate_limit(bot: BotService, engine: Engine) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=OWNER, prompt="old").job_id
        assert jid is not None
        for _ in range(3):
            s.add(
                AiUsage(
                    job_id=jid,
                    provider="deepseek",
                    model="m",
                    status="ok",
                    created_at=utcnow() - timedelta(minutes=5),
                )
            )
        jobs.transition(s, jid, JobState.FAILED, error_code="X")
    assert "received" in _text(bot.handle_text(5, OWNER, "new request")).lower()


# ---- commands ----------------------------------------------------------------------------


def test_start_greets(bot: BotService) -> None:
    assert "Luna Cafe" in _text(bot.start(OWNER))


def test_status_without_and_with_job(bot: BotService) -> None:
    assert "no active" in _text(bot.status(OWNER)).lower()
    bot.handle_text(1, OWNER, "Change the Latte price to 150 TL.")
    text = _text(bot.status(OWNER))
    assert "QUEUED" in text and "Latte" in text


def test_history_lists_recent_jobs(bot: BotService, engine: Engine) -> None:
    assert "no jobs" in _text(bot.history(OWNER)).lower()
    bot.handle_text(1, OWNER, "first request")
    assert "first request" in _text(bot.history(OWNER))


def test_cancel_without_active_job(bot: BotService) -> None:
    assert "no active" in _text(bot.cancel(OWNER)).lower()


def test_cancel_is_idempotent_and_signals_the_worker(
    bot: BotService, engine: Engine, signals: CancelSignals
) -> None:
    bot.handle_text(1, OWNER, "do something")
    [job] = _jobs(engine)
    first, second = bot.cancel(OWNER), bot.cancel(OWNER)
    assert "requested" in _text(first).lower()
    assert "already" in _text(second).lower()
    assert signals.event(job.id).is_set()
    with Session(engine) as s:
        assert jobs.get_job(s, job.id).cancel_requested is True


# ---- approval ------------------------------------------------------------------------------


def test_approve_publishes_exactly_once(bot: BotService, engine: Engine) -> None:
    bot.handle_text(1, OWNER, "Change the Latte price to 150 TL.")
    jid, draft = _awaiting(engine)
    data = _callback("a", jid, draft)
    assert "published" in _text(bot.handle_callback(OWNER, data)).lower()
    assert "already" in _text(bot.handle_callback(OWNER, data)).lower()
    assert "already" in _text(bot.handle_callback(OWNER, data)).lower()
    with Session(engine) as s:
        prices = {i.id: str(i.price) for i in publishing.get_published(s)[1].menu}
        assert prices["latte"] == "150"
        assert len(s.scalars(select(ContentVersion)).all()) == 2


def test_reject_leaves_site_unchanged(bot: BotService, engine: Engine) -> None:
    bot.handle_text(1, OWNER, "Change the Latte price to 150 TL.")
    jid, draft = _awaiting(engine)
    assert "rejected" in _text(bot.handle_callback(OWNER, _callback("r", jid, draft))).lower()
    with Session(engine) as s:
        prices = {i.id: str(i.price) for i in publishing.get_published(s)[1].menu}
        assert prices["latte"] == "130"


def test_callback_from_other_user_does_nothing(bot: BotService, engine: Engine) -> None:
    bot.handle_text(1, OWNER, "x")
    jid, draft = _awaiting(engine)
    assert bot.handle_callback(STRANGER, _callback("a", jid, draft)) is None
    with Session(engine) as s:
        assert jobs.get_job(s, jid).state == JobState.AWAITING_APPROVAL


def test_callback_with_wrong_draft_is_stale(bot: BotService, engine: Engine) -> None:
    bot.handle_text(1, OWNER, "x")
    jid, _ = _awaiting(engine)
    reply = bot.handle_callback(OWNER, f"a:{jid}:{'f' * 16}")
    assert "no longer valid" in _text(reply).lower()
    with Session(engine) as s:
        assert jobs.get_job(s, jid).state == JobState.AWAITING_APPROVAL


@pytest.mark.parametrize(
    "junk",
    [
        "",
        "a",
        "a:b:c",
        "x:" + "0" * 32 + ":" + "0" * 16,
        "a:" + "z" * 32 + ":" + "0" * 16,
        "a" * 200,
    ],
)
def test_malformed_callbacks_are_ignored(bot: BotService, junk: str) -> None:
    assert bot.handle_callback(OWNER, junk) is None


def test_callback_for_unknown_job_is_ignored(bot: BotService) -> None:
    assert bot.handle_callback(OWNER, "a:" + "0" * 32 + ":" + "0" * 16) is None


def test_approve_after_cancel_request_does_not_publish(bot: BotService, engine: Engine) -> None:
    bot.handle_text(1, OWNER, "x")
    jid, draft = _awaiting(engine)
    bot.cancel(OWNER)
    reply = bot.handle_callback(OWNER, _callback("a", jid, draft))
    assert "cancel" in _text(reply).lower()
    with Session(engine) as s:
        prices = {i.id: str(i.price) for i in publishing.get_published(s)[1].menu}
        assert prices["latte"] == "130"
