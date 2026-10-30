from datetime import timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from muda.domain.job_state import JobState
from muda.domain.operations import UpdateDesign, UpdateMenuPrice, parse_operation
from muda.domain.site import SiteContent, initial_content
from muda.persistence import jobs, publishing
from muda.persistence.models import ContentVersion, Job, JobEvent, utcnow

USER = 42


def _price_op() -> UpdateMenuPrice:
    op = parse_operation(
        {"operation": "update_menu_price", "product_id": "latte", "new_price": "150"}
    )
    assert isinstance(op, UpdateMenuPrice)
    return op


def _design_op() -> UpdateDesign:
    op = parse_operation(
        {"operation": "update_design", "changes": {"theme": "dark", "hero_alignment": "center"}}
    )
    assert isinstance(op, UpdateDesign)
    return op


def _awaiting(
    engine: Engine, op: UpdateMenuPrice | UpdateDesign, update_id: int = 1
) -> tuple[str, str]:
    """Create a job, move it to PROCESSING and create the draft. Returns (job_id, draft_id)."""
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=update_id, telegram_user_id=USER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
        draft_id = publishing.create_draft(s, jid, op)
    return jid, draft_id


def _published_content(engine: Engine) -> SiteContent:
    with Session(engine) as s:
        return publishing.get_published(s)[1]


@pytest.fixture(autouse=True)
def _seed(engine: Engine) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)


def test_seed_is_idempotent(engine: Engine) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        publishing.seed_initial(s)
    with Session(engine) as s:
        assert len(s.scalars(select(ContentVersion)).all()) == 1
        assert publishing.get_published(s)[1] == initial_content()


def test_draft_does_not_change_live_site(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    assert _published_content(engine) == initial_content()
    with Session(engine) as s:
        job = jobs.get_job(s, jid)
        assert job.state == JobState.AWAITING_APPROVAL
        assert job.draft_version_id == draft_id
        draft = publishing.get_version_content(s, draft_id)
        assert {i.id: str(i.price) for i in draft.menu}["latte"] == "150"


def test_approve_publishes_price_change(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.approve(s, jid, draft_id, USER) == "published"
    prices = {i.id: str(i.price) for i in _published_content(engine).menu}
    assert prices["latte"] == "150"
    with Session(engine) as s:
        assert jobs.get_job(s, jid).state == JobState.PUBLISHED
        assert jobs.get_active_job(s) is None
        states = [e.to_state for e in s.scalars(select(JobEvent).where(JobEvent.job_id == jid))]
        assert states == ["QUEUED", "PROCESSING", "AWAITING_APPROVAL", "PUBLISHING", "PUBLISHED"]


def test_approve_publishes_design_change(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _design_op())
    assert _published_content(engine).design.theme == "light"
    with Session(engine) as s, jobs.write_tx(s):
        publishing.approve(s, jid, draft_id, USER)
    live = _published_content(engine)
    assert live.design.theme == "dark"
    assert live.design.hero_alignment == "center"


def test_duplicate_approval_publishes_once(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    results = []
    for _ in range(3):
        with Session(engine) as s, jobs.write_tx(s):
            results.append(publishing.approve(s, jid, draft_id, USER))
    assert results == ["published", "already_processed", "already_processed"]
    with Session(engine) as s:
        assert len(s.scalars(select(ContentVersion)).all()) == 2


def test_approve_with_wrong_draft_version_is_refused(engine: Engine) -> None:
    jid, _ = _awaiting(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.approve(s, jid, "f" * 32, USER) == "stale_version"
    assert _published_content(engine) == initial_content()
    with Session(engine) as s:
        assert jobs.get_job(s, jid).state == JobState.AWAITING_APPROVAL


def test_approve_by_other_user_is_refused(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.approve(s, jid, draft_id, USER + 1) == "unauthorized"
    assert _published_content(engine) == initial_content()


def test_reject_leaves_live_site_unchanged_and_is_idempotent(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    for expected in ("rejected", "already_processed"):
        with Session(engine) as s, jobs.write_tx(s):
            assert publishing.reject(s, jid, draft_id, USER) == expected
    assert _published_content(engine) == initial_content()
    with Session(engine) as s:
        assert jobs.get_job(s, jid).state == JobState.REJECTED


def test_approve_after_reject_cannot_publish(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        publishing.reject(s, jid, draft_id, USER)
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.approve(s, jid, draft_id, USER) == "already_processed"
    assert _published_content(engine) == initial_content()


def test_cancel_before_approval_blocks_publish(engine: Engine) -> None:
    jid, draft_id = _awaiting(engine, _price_op())
    with Session(engine) as s, jobs.write_tx(s):
        jobs.request_cancel(s, jid)
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.approve(s, jid, draft_id, USER) == "cancelled"
    assert _published_content(engine) == initial_content()


def test_cancel_during_processing_prevents_draft(engine: Engine) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=USER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
        jobs.request_cancel(s, jid)
    with Session(engine) as s, jobs.write_tx(s):
        with pytest.raises(publishing.JobCancelledError):
            publishing.create_draft(s, jid, _price_op())
        publishing.finish_cancellation(s, jid)
    with Session(engine) as s:
        job = jobs.get_job(s, jid)
        assert job.state == JobState.CANCELLED
        assert job.draft_version_id is None
        assert len(s.scalars(select(ContentVersion)).all()) == 1


def test_unknown_product_draft_fails_job_without_version(engine: Engine) -> None:
    op = UpdateMenuPrice(
        operation="update_menu_price", product_id="pizza", new_price=_price_op().new_price
    )
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=USER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
    with Session(engine) as s, jobs.write_tx(s):
        with pytest.raises(publishing.InvalidProposalError):
            publishing.create_draft(s, jid, op)
        jobs.transition(s, jid, JobState.FAILED, error_code="UNKNOWN_PRODUCT")
    with Session(engine) as s:
        assert len(s.scalars(select(ContentVersion)).all()) == 1


def test_content_versions_are_immutable(engine: Engine) -> None:
    with pytest.raises((StatementError, ValueError)), Session(engine) as s, jobs.write_tx(s):
        v = s.scalars(select(ContentVersion)).one()
        v.snapshot = {"hacked": True}
        s.flush()


def test_claim_next_job_moves_queued_to_processing(engine: Engine) -> None:
    assert _claim(engine) is None
    with Session(engine) as s, jobs.write_tx(s):
        jobs.create_job(s, update_id=1, telegram_user_id=USER, prompt="p")
    claimed = _claim(engine)
    assert claimed is not None
    assert _claim(engine) is None


def _claim(engine: Engine) -> str | None:
    with Session(engine) as s, jobs.write_tx(s):
        return publishing.claim_next_job(s)


def test_recovery_after_restart(engine: Engine) -> None:
    # PROCESSING interrupted -> FAILED (AI call may already be billed; never silently retry)
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=1, telegram_user_id=USER, prompt="p").job_id
        assert jid is not None
        jobs.transition(s, jid, JobState.PROCESSING)
    with Session(engine) as s, jobs.write_tx(s):
        report = publishing.recover_interrupted(s)
    assert report == {"failed": 1, "cancelled": 0}
    with Session(engine) as s:
        job = jobs.get_job(s, jid)
        assert job.state == JobState.FAILED
        assert job.error_code == "INTERRUPTED"

    # CANCELLING interrupted -> CANCELLED, QUEUED left for the worker
    with Session(engine) as s, jobs.write_tx(s):
        j2 = jobs.create_job(s, update_id=2, telegram_user_id=USER, prompt="p").job_id
        assert j2 is not None
        jobs.request_cancel(s, j2)
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.recover_interrupted(s) == {"failed": 0, "cancelled": 1}

    with Session(engine) as s, jobs.write_tx(s):
        j3 = jobs.create_job(s, update_id=3, telegram_user_id=USER, prompt="p").job_id
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.recover_interrupted(s) == {"failed": 0, "cancelled": 0}
    with Session(engine) as s:
        assert j3 is not None
        assert jobs.get_job(s, j3).state == JobState.QUEUED


def test_expire_stale_awaiting_approval(engine: Engine) -> None:
    jid, _ = _awaiting(engine, _price_op())
    ttl = timedelta(minutes=30)
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.expire_stale(s, ttl, now=utcnow() + timedelta(minutes=10)) == 0
    with Session(engine) as s, jobs.write_tx(s):
        assert publishing.expire_stale(s, ttl, now=utcnow() + timedelta(minutes=31)) == 1
    with Session(engine) as s:
        job = s.get(Job, jid)
        assert job is not None
        assert job.state == JobState.EXPIRED
    assert _published_content(engine) == initial_content()
