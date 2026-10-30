"""Explicit job state machine. Every status change must go through ensure_transition."""

from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"  # AI call + validation + draft + preview generation
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class InvalidTransitionError(Exception):
    def __init__(self, current: JobState, target: JobState) -> None:
        super().__init__(f"Invalid job transition: {current} -> {target}")
        self.current = current
        self.target = target


_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.PROCESSING, JobState.CANCELLING, JobState.FAILED}),
    JobState.PROCESSING: frozenset(
        {JobState.AWAITING_APPROVAL, JobState.CANCELLING, JobState.FAILED}
    ),
    JobState.AWAITING_APPROVAL: frozenset(
        {JobState.PUBLISHING, JobState.REJECTED, JobState.CANCELLING, JobState.EXPIRED}
    ),
    # Once publishing starts it is no longer cancellable: it either commits or fails.
    JobState.PUBLISHING: frozenset({JobState.PUBLISHED, JobState.FAILED}),
    JobState.CANCELLING: frozenset({JobState.CANCELLED, JobState.FAILED}),
    JobState.PUBLISHED: frozenset(),
    JobState.CANCELLED: frozenset(),
    JobState.REJECTED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.EXPIRED: frozenset(),
}

ACTIVE_STATES: frozenset[JobState] = frozenset(
    {
        JobState.QUEUED,
        JobState.PROCESSING,
        JobState.AWAITING_APPROVAL,
        JobState.PUBLISHING,
        JobState.CANCELLING,
    }
)


def ensure_transition(current: JobState, target: JobState) -> None:
    if target not in _TRANSITIONS[current]:
        raise InvalidTransitionError(current, target)
