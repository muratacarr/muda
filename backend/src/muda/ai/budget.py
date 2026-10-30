"""AI usage recording and application-level cost protection (not a provider billing limit)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muda.ai.pricing import PricingTable, usage_cost, worst_case_cost
from muda.ai.types import TokenUsage
from muda.persistence.models import AiUsage


@dataclass(frozen=True)
class BudgetLimits:
    max_calls_per_job: int
    per_job_usd: Decimal
    monthly_usd: Decimal


class BudgetExceededError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def record_usage(
    session: Session,
    *,
    job_id: str,
    provider: str,
    model: str,
    usage: TokenUsage,
    pricing: PricingTable,
    latency_ms: int,
    status: str,
    now: datetime,
) -> None:
    """Persist one AI request (success or failure). Must run inside write_tx()."""
    session.add(
        AiUsage(
            job_id=job_id,
            provider=provider,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_hit_tokens=usage.cache_hit_tokens,
            cache_miss_tokens=usage.cache_miss_tokens,
            cost_usd=usage_cost(pricing, model, usage, now),
            pricing_version=pricing.version,
            latency_ms=latency_ms,
            status=status,
            created_at=now,
        )
    )


def _spent(
    session: Session, unknown_cost: Decimal, *, job_id: str | None, since: datetime | None
) -> Decimal:
    """Known costs plus a pessimistic charge for every request whose cost is unknown."""
    stmt = select(AiUsage.cost_usd)
    if job_id is not None:
        stmt = stmt.where(AiUsage.job_id == job_id)
    if since is not None:
        stmt = stmt.where(AiUsage.created_at >= since)
    costs = session.scalars(stmt).all()
    return sum((c if c is not None else unknown_cost for c in costs), Decimal(0))


def ensure_can_call(
    session: Session,
    *,
    job_id: str,
    limits: BudgetLimits,
    pricing: PricingTable,
    model: str,
    est_input_tokens: int,
    max_output_tokens: int,
    now: datetime,
) -> None:
    """Raise BudgetExceededError unless a call fits all limits, reserving its worst-case cost."""
    calls = session.scalar(
        select(func.count()).select_from(AiUsage).where(AiUsage.job_id == job_id)
    )
    if (calls or 0) >= limits.max_calls_per_job:
        raise BudgetExceededError("AI_CALL_LIMIT")
    reservation = worst_case_cost(
        pricing, model, input_tokens=est_input_tokens, max_output_tokens=max_output_tokens
    )
    if reservation is None:
        raise BudgetExceededError("AI_PRICING_UNKNOWN")
    if (
        _spent(session, limits.per_job_usd, job_id=job_id, since=None) + reservation
        > limits.per_job_usd
    ):
        raise BudgetExceededError("AI_JOB_BUDGET")
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    spent_month = _spent(session, limits.per_job_usd, job_id=None, since=month_start)
    if spent_month + reservation > limits.monthly_usd:
        raise BudgetExceededError("AI_MONTHLY_BUDGET")
