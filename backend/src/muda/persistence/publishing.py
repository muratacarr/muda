"""Draft creation, approval, rejection, publishing and recovery. Run inside jobs.write_tx()."""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from muda.domain.job_state import JobState
from muda.domain.operations import UpdateDesign, UpdateMenuPrice
from muda.domain.site import SiteContent, UnknownProductError, apply_operation, initial_content
from muda.persistence import jobs
from muda.persistence.models import ContentVersion, Job, SiteState, utcnow

DecisionResult = Literal[
    "published", "rejected", "already_processed", "stale_version", "unauthorized", "cancelled"
]


class JobCancelledError(Exception):
    """Cancellation was requested; no draft may be created."""


class InvalidProposalError(Exception):
    """The proposal passed schema validation but is not applicable to the current site."""


@event.listens_for(ContentVersion, "before_update")
def _versions_are_immutable(*_: object) -> None:
    raise ValueError("content versions are immutable")


def seed_initial(session: Session) -> None:
    if session.get(SiteState, 1) is not None:
        return
    version = ContentVersion(snapshot=initial_content().model_dump(mode="json"))
    session.add(version)
    session.flush()
    session.add(SiteState(id=1, published_version_id=version.id))


def get_version_content(session: Session, version_id: str) -> SiteContent:
    version = session.get(ContentVersion, version_id)
    if version is None:
        raise LookupError(version_id)
    return SiteContent.model_validate(version.snapshot)


def get_published(session: Session) -> tuple[str, SiteContent]:
    state = session.get(SiteState, 1)
    if state is None:
        raise LookupError("site not seeded")
    return state.published_version_id, get_version_content(session, state.published_version_id)


def create_draft(session: Session, job_id: str, op: UpdateMenuPrice | UpdateDesign) -> str:
    """Create an immutable draft version from the published one; job -> AWAITING_APPROVAL."""
    job = jobs.get_job(session, job_id)
    if job.cancel_requested or job.state == JobState.CANCELLING:
        raise JobCancelledError(job_id)
    base_id, base = get_published(session)
    try:
        content = apply_operation(base, op)
    except UnknownProductError as exc:
        raise InvalidProposalError(str(exc)) from exc
    draft = ContentVersion(parent_id=base_id, snapshot=content.model_dump(mode="json"))
    session.add(draft)
    session.flush()
    jobs.transition(session, job_id, JobState.AWAITING_APPROVAL)
    job.draft_version_id = draft.id
    return draft.id


def _precheck(job: Job, draft_version_id: str, user_id: int) -> DecisionResult | None:
    if job.telegram_user_id != user_id:
        return "unauthorized"
    if job.cancel_requested or job.state in (JobState.CANCELLING, JobState.CANCELLED):
        return "cancelled"
    if job.state != JobState.AWAITING_APPROVAL:
        return "already_processed"
    if job.draft_version_id != draft_version_id:
        return "stale_version"
    return None


def approve(session: Session, job_id: str, draft_version_id: str, user_id: int) -> DecisionResult:
    """Publish the draft atomically: state change and live-pointer swap share one transaction."""
    job = jobs.get_job(session, job_id)
    refused = _precheck(job, draft_version_id, user_id)
    if refused is not None:
        return refused
    published_id, _ = get_published(session)
    draft = session.get(ContentVersion, draft_version_id)
    if draft is None or draft.parent_id != published_id:
        jobs.transition(session, job_id, JobState.FAILED, error_code="STALE_BASE")
        return "stale_version"
    jobs.transition(session, job_id, JobState.PUBLISHING)
    state = session.get(SiteState, 1)
    if state is None:
        raise LookupError("site not seeded")
    state.published_version_id = draft.id
    jobs.transition(session, job_id, JobState.PUBLISHED)
    return "published"


def reject(session: Session, job_id: str, draft_version_id: str, user_id: int) -> DecisionResult:
    job = jobs.get_job(session, job_id)
    refused = _precheck(job, draft_version_id, user_id)
    if refused is not None:
        return refused
    jobs.transition(session, job_id, JobState.REJECTED)
    return "rejected"


def finish_cancellation(session: Session, job_id: str) -> None:
    jobs.transition(session, job_id, JobState.CANCELLED)


def claim_next_job(session: Session) -> str | None:
    job = session.scalars(
        select(Job).where(Job.state == JobState.QUEUED).order_by(Job.created_at).limit(1)
    ).first()
    if job is None:
        return None
    jobs.transition(session, job.id, JobState.PROCESSING)
    return job.id


def recover_interrupted(session: Session) -> dict[str, int]:
    """Startup recovery. PROCESSING is failed, never retried: the AI call may already be billed."""
    failed = cancelled = 0
    for job in session.scalars(
        select(Job).where(
            Job.state.in_([JobState.PROCESSING, JobState.PUBLISHING, JobState.CANCELLING])
        )
    ).all():
        if job.state == JobState.CANCELLING:
            jobs.transition(session, job.id, JobState.CANCELLED)
            cancelled += 1
        else:
            jobs.transition(session, job.id, JobState.FAILED, error_code="INTERRUPTED")
            failed += 1
    return {"failed": failed, "cancelled": cancelled}


def expire_stale(session: Session, ttl: timedelta, *, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - ttl
    stale = session.scalars(
        select(Job).where(Job.state == JobState.AWAITING_APPROVAL, Job.updated_at < cutoff)
    ).all()
    for job in stale:
        jobs.transition(session, job.id, JobState.EXPIRED)
    return len(stale)


def finalize_cancelling(session: Session) -> list[str]:
    """Complete cancellations nobody is processing. Only call when no job is in flight."""
    ids = list(session.scalars(select(Job.id).where(Job.state == JobState.CANCELLING)))
    for job_id in ids:
        jobs.transition(session, job_id, JobState.CANCELLED)
    return ids
