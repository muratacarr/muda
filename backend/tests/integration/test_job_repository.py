from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from muda.domain.job_state import InvalidTransitionError, JobState
from muda.persistence import jobs
from muda.persistence.models import JobEvent

USER = 42


def _create(engine: Engine, update_id: int, prompt: str = "hi") -> jobs.CreateResult:
    with Session(engine) as s, jobs.write_tx(s):
        return jobs.create_job(s, update_id=update_id, telegram_user_id=USER, prompt=prompt)


def test_wal_and_foreign_keys_enabled(engine: Engine) -> None:
    with engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_create_job_is_queued_with_event(engine: Engine) -> None:
    res = _create(engine, 1)
    assert res.status == "created"
    with Session(engine) as s:
        assert res.job_id is not None
        job = jobs.get_job(s, res.job_id)
        assert job.state == JobState.QUEUED
        assert job.cancel_requested is False
        events = s.scalars(select(JobEvent).where(JobEvent.job_id == job.id)).all()
        assert [e.to_state for e in events] == ["QUEUED"]


def test_duplicate_update_does_not_create_second_job(engine: Engine) -> None:
    first = _create(engine, 7)
    again = _create(engine, 7)
    assert again.status == "duplicate"
    assert again.job_id is None
    with Session(engine) as s:
        assert len(jobs.list_recent(s, 10)) == 1
    assert first.status == "created"


def test_second_prompt_rejected_while_job_active(engine: Engine) -> None:
    _create(engine, 1)
    res = _create(engine, 2)
    assert res.status == "active_job_exists"


def test_rejected_prompt_update_is_still_recorded_as_processed(engine: Engine) -> None:
    _create(engine, 1)
    assert _create(engine, 2).status == "active_job_exists"
    # redelivery of update 2 must stay a no-op even after the active job finished
    with Session(engine) as s, jobs.write_tx(s):
        job = jobs.get_active_job(s)
        assert job is not None
        jobs.transition(s, job.id, JobState.FAILED, error_code="X")
    assert _create(engine, 2).status == "duplicate"


def test_new_job_allowed_after_terminal_state(engine: Engine) -> None:
    first = _create(engine, 1)
    with Session(engine) as s, jobs.write_tx(s):
        assert first.job_id is not None
        jobs.transition(s, first.job_id, JobState.FAILED, error_code="X")
    assert _create(engine, 2).status == "created"


def test_db_constraint_blocks_two_active_jobs_even_bypassing_service(engine: Engine) -> None:
    from sqlalchemy.exc import IntegrityError

    from muda.persistence.models import Job

    _create(engine, 1)
    with pytest.raises(IntegrityError), Session(engine) as s, jobs.write_tx(s):
        s.add(Job(id="rogue", prompt="p", telegram_user_id=USER, state=JobState.QUEUED))


def test_transition_validates_and_records_event(engine: Engine) -> None:
    jid = _create(engine, 1).job_id
    assert jid is not None
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, jid, JobState.PROCESSING)
    with Session(engine) as s, jobs.write_tx(s), pytest.raises(InvalidTransitionError):
        jobs.transition(s, jid, JobState.PUBLISHED)
    with Session(engine) as s:
        events = s.scalars(select(JobEvent).where(JobEvent.job_id == jid)).all()
        assert [e.to_state for e in events] == ["QUEUED", "PROCESSING"]


def test_terminal_transition_sets_completed_at(engine: Engine) -> None:
    jid = _create(engine, 1).job_id
    assert jid is not None
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, jid, JobState.FAILED, error_code="AI_TIMEOUT")
    with Session(engine) as s:
        job = jobs.get_job(s, jid)
        assert job.completed_at is not None
        assert job.error_code == "AI_TIMEOUT"


def test_request_cancel_is_idempotent(engine: Engine) -> None:
    jid = _create(engine, 1).job_id
    assert jid is not None
    for _ in range(3):
        with Session(engine) as s, jobs.write_tx(s):
            assert jobs.request_cancel(s, jid) in {"requested", "already_requested"}
    with Session(engine) as s:
        job = jobs.get_job(s, jid)
        assert job.cancel_requested is True
        assert job.state == JobState.CANCELLING
        events = s.scalars(select(JobEvent).where(JobEvent.job_id == jid)).all()
        assert [e.to_state for e in events].count("CANCELLING") == 1


def test_cancel_not_possible_while_publishing_or_terminal(engine: Engine) -> None:
    jid = _create(engine, 1).job_id
    assert jid is not None
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, jid, JobState.PROCESSING)
        jobs.transition(s, jid, JobState.AWAITING_APPROVAL)
        jobs.transition(s, jid, JobState.PUBLISHING)
    with Session(engine) as s, jobs.write_tx(s):
        assert jobs.request_cancel(s, jid) == "not_cancellable"


def test_concurrent_creations_yield_exactly_one_job(engine: Engine) -> None:
    def attempt(i: int) -> str:
        return _create(engine, 1000 + i).status

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(16)))
    assert results.count("created") == 1
    assert results.count("active_job_exists") == 15


def test_concurrent_same_update_creates_one_job(engine: Engine) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _create(engine, 99).status, range(12)))
    assert results.count("created") == 1
    assert results.count("duplicate") == 11


def test_concurrent_transition_only_one_wins(engine: Engine) -> None:
    jid = _create(engine, 1).job_id
    assert jid is not None

    def go(_: int) -> str:
        try:
            with Session(engine) as s, jobs.write_tx(s):
                jobs.transition(s, jid, JobState.PROCESSING)
            return "ok"
        except InvalidTransitionError:
            return "lost"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(go, range(6)))
    assert results.count("ok") == 1
