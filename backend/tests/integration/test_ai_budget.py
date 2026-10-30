from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from muda.ai import budget
from muda.ai.budget import BudgetExceededError, BudgetLimits
from muda.ai.pricing import load_pricing
from muda.ai.types import TokenUsage
from muda.domain.job_state import JobState
from muda.persistence import jobs
from muda.persistence.models import AiUsage

PRICING = load_pricing()
MODEL = "deepseek-flash"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
LIMITS = BudgetLimits(max_calls_per_job=2, per_job_usd=Decimal("0.05"), monthly_usd=Decimal("1"))


def _job(engine: Engine, update_id: int = 1) -> str:
    with Session(engine) as s, jobs.write_tx(s):
        jid = jobs.create_job(s, update_id=update_id, telegram_user_id=1, prompt="p").job_id
    assert jid is not None
    return jid


def _check(engine: Engine, jid: str, input_tokens: int = 2000, max_out: int = 500) -> None:
    with Session(engine) as s, jobs.write_tx(s):
        budget.ensure_can_call(
            s,
            job_id=jid,
            limits=LIMITS,
            pricing=PRICING,
            model=MODEL,
            est_input_tokens=input_tokens,
            max_output_tokens=max_out,
            now=NOW,
        )


def _record(engine: Engine, jid: str, usage: TokenUsage, status: str = "ok") -> None:
    with Session(engine) as s, jobs.write_tx(s):
        budget.record_usage(
            s,
            job_id=jid,
            provider="deepseek",
            model=MODEL,
            usage=usage,
            pricing=PRICING,
            latency_ms=120,
            status=status,
            now=NOW,
        )


def test_fresh_job_is_allowed(engine: Engine) -> None:
    _check(engine, _job(engine))


def test_record_usage_stores_decimal_cost_and_pricing_version(engine: Engine) -> None:
    jid = _job(engine)
    _record(engine, jid, TokenUsage(input_tokens=1000, output_tokens=100, cache_hit_tokens=0))
    with Session(engine) as s:
        row = s.query(AiUsage).one()
        assert row.job_id == jid
        assert row.cost_usd == Decimal("0.00021")  # off-peak: (1000*0.15 + 100*0.6) / 1M
        assert row.pricing_version == PRICING.version
        assert row.status == "ok"


def test_unreported_usage_is_stored_as_unknown_cost(engine: Engine) -> None:
    jid = _job(engine)
    _record(engine, jid, TokenUsage(), status="error")
    with Session(engine) as s:
        row = s.query(AiUsage).one()
        assert row.cost_usd is None


def test_max_calls_per_job(engine: Engine) -> None:
    jid = _job(engine)
    for _ in range(2):
        _record(engine, jid, TokenUsage(input_tokens=10, output_tokens=10))
    with pytest.raises(BudgetExceededError) as exc:
        _check(engine, jid)
    assert exc.value.code == "AI_CALL_LIMIT"


def test_per_job_cost_limit_uses_worst_case_reservation(engine: Engine) -> None:
    jid = _job(engine)
    # worst case: 1M input * 0.3 + 100k output * 1.2 / 1M = 0.42 USD > 0.05
    with pytest.raises(BudgetExceededError) as exc:
        _check(engine, jid, input_tokens=1_000_000, max_out=100_000)
    assert exc.value.code == "AI_JOB_BUDGET"


def test_monthly_budget_blocks_when_spent_plus_reservation_exceeds(engine: Engine) -> None:
    j1 = _job(engine, 1)
    _record(
        engine, j1, TokenUsage(input_tokens=3_000_000, output_tokens=1_200_000)
    )  # off-peak ~1.17
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, j1, JobState.FAILED)
    j2 = _job(engine, 2)
    with pytest.raises(BudgetExceededError) as exc:
        _check(engine, j2)
    assert exc.value.code == "AI_MONTHLY_BUDGET"


def test_unknown_cost_rows_count_as_worst_case_against_monthly_budget(engine: Engine) -> None:
    j1 = _job(engine, 1)
    for _ in range(5):
        _record(engine, j1, TokenUsage(), status="error")
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, j1, JobState.FAILED)
    j2 = _job(engine, 2)
    # each unknown-cost row is charged pessimistically at the per-job limit
    limits = BudgetLimits(
        max_calls_per_job=2, per_job_usd=Decimal("0.5"), monthly_usd=Decimal("0.5")
    )
    with Session(engine) as s, jobs.write_tx(s), pytest.raises(BudgetExceededError):
        budget.ensure_can_call(
            s,
            job_id=j2,
            limits=limits,
            pricing=PRICING,
            model=MODEL,
            est_input_tokens=2000,
            max_output_tokens=500,
            now=NOW,
        )


def test_previous_month_spend_does_not_count(engine: Engine) -> None:
    j1 = _job(engine, 1)
    with Session(engine) as s, jobs.write_tx(s):
        budget.record_usage(
            s,
            job_id=j1,
            provider="deepseek",
            model=MODEL,
            usage=TokenUsage(input_tokens=5_000_000, output_tokens=1_000_000),
            pricing=PRICING,
            latency_ms=1,
            status="ok",
            now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
    with Session(engine) as s, jobs.write_tx(s):
        jobs.transition(s, j1, JobState.FAILED)
    # j1 is not counted for per-job calls of a new job either
    j2 = _job(engine, 2)
    _check(engine, j2)
