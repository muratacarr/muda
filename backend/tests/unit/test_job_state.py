import pytest

from muda.domain.job_state import (
    ACTIVE_STATES,
    InvalidTransitionError,
    JobState,
    ensure_transition,
)


def test_happy_path_is_allowed() -> None:
    path = [
        JobState.QUEUED,
        JobState.PROCESSING,
        JobState.AWAITING_APPROVAL,
        JobState.PUBLISHING,
        JobState.PUBLISHED,
    ]
    for current, nxt in zip(path, path[1:], strict=False):
        ensure_transition(current, nxt)


def test_terminal_states_cannot_transition() -> None:
    for terminal in (
        JobState.PUBLISHED,
        JobState.CANCELLED,
        JobState.REJECTED,
        JobState.FAILED,
        JobState.EXPIRED,
    ):
        assert terminal not in ACTIVE_STATES
        for target in JobState:
            with pytest.raises(InvalidTransitionError):
                ensure_transition(terminal, target)


def test_cannot_skip_approval() -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(JobState.PROCESSING, JobState.PUBLISHING)
    with pytest.raises(InvalidTransitionError):
        ensure_transition(JobState.QUEUED, JobState.PUBLISHED)


def test_cancelling_is_reachable_from_every_active_state_except_publishing_commit() -> None:
    for state in (JobState.QUEUED, JobState.PROCESSING, JobState.AWAITING_APPROVAL):
        ensure_transition(state, JobState.CANCELLING)
    ensure_transition(JobState.CANCELLING, JobState.CANCELLED)


def test_cancelling_cannot_publish() -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(JobState.CANCELLING, JobState.PUBLISHING)
    with pytest.raises(InvalidTransitionError):
        ensure_transition(JobState.CANCELLING, JobState.PUBLISHED)


def test_publishing_cannot_be_cancelled() -> None:
    with pytest.raises(InvalidTransitionError):
        ensure_transition(JobState.PUBLISHING, JobState.CANCELLING)
