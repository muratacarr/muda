"""Job repository. All state changes go through here inside write_tx()."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from muda.domain.job_state import ACTIVE_STATES, JobState, ensure_transition
from muda.persistence.models import Job, JobEvent, ProcessedUpdate, utcnow

CreateStatus = Literal["created", "duplicate", "active_job_exists"]
CancelStatus = Literal["requested", "already_requested", "not_cancellable"]
_CANCELLABLE = frozenset({JobState.QUEUED, JobState.PROCESSING, JobState.AWAITING_APPROVAL})


@dataclass(frozen=True)
class CreateResult:
    status: CreateStatus
    job_id: str | None = None


@contextmanager
def write_tx(session: Session) -> Iterator[Session]:
    """Take the SQLite write lock up front (BEGIN IMMEDIATE); commit or roll back."""
    session.execute(text("BEGIN IMMEDIATE"))
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise


def get_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise LookupError(job_id)
    return job


def get_active_job(session: Session) -> Job | None:
    return session.scalars(select(Job).where(Job.state.in_(ACTIVE_STATES))).first()


def list_recent(session: Session, limit: int) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)))


def _add_event(
    session: Session, job_id: str, from_state: str | None, to_state: str, detail: str | None = None
) -> None:
    session.add(JobEvent(job_id=job_id, from_state=from_state, to_state=to_state, detail=detail))


def create_job(
    session: Session, *, update_id: int, telegram_user_id: int, prompt: str
) -> CreateResult:
    """Idempotent, atomic job creation. Must run inside write_tx()."""
    if session.get(ProcessedUpdate, update_id) is not None:
        return CreateResult("duplicate")
    # Recorded even when rejected so a redelivered update never retries later.
    session.add(ProcessedUpdate(update_id=update_id))
    if get_active_job(session) is not None:
        return CreateResult("active_job_exists")
    job = Job(prompt=prompt, telegram_user_id=telegram_user_id, state=JobState.QUEUED)
    session.add(job)
    session.flush()
    _add_event(session, job.id, None, JobState.QUEUED)
    return CreateResult("created", job.id)


def transition(
    session: Session, job_id: str, target: JobState, *, error_code: str | None = None
) -> Job:
    """Validated state change plus event. Must run inside write_tx()."""
    job = get_job(session, job_id)
    current = JobState(job.state)
    ensure_transition(current, target)
    job.state = target
    job.updated_at = utcnow()
    if error_code is not None:
        job.error_code = error_code
    if target not in ACTIVE_STATES:
        job.completed_at = job.updated_at
    _add_event(session, job.id, current, target, error_code)
    return job


def request_cancel(session: Session, job_id: str) -> CancelStatus:
    """Idempotent cancellation request. Must run inside write_tx()."""
    job = get_job(session, job_id)
    state = JobState(job.state)
    if state == JobState.CANCELLING:
        return "already_requested"
    if state not in _CANCELLABLE:
        return "not_cancellable"
    job.cancel_requested = True
    transition(session, job_id, JobState.CANCELLING)
    return "requested"
