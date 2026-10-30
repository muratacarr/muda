from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    literal_column,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from muda.domain.job_state import ACTIVE_STATES, JobState


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite drops tzinfo; always store and return timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class DecimalText(TypeDecorator[Decimal]):
    """Exact Decimal storage. SQLite NUMERIC would round-trip through float."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        return None if value is None else Decimal(value)


class Base(DeclarativeBase):
    pass


class ContentVersion(Base):
    """Immutable snapshot of site content + design. Never updated after insert."""

    __tablename__ = "content_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("content_versions.id"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class SiteState(Base):
    """Singleton row pointing at the currently published version."""

    __tablename__ = "site_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    published_version_id: Mapped[str] = mapped_column(ForeignKey("content_versions.id"))
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # At most one active job globally, enforced by the database itself.
        Index(
            "uq_one_active_job",
            literal_column("1"),
            unique=True,
            sqlite_where=text(
                "state IN (" + ",".join(f"'{s.value}'" for s in sorted(ACTIVE_STATES)) + ")"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    prompt: Mapped[str] = mapped_column(Text)
    telegram_user_id: Mapped[int] = mapped_column(Integer)
    state: Mapped[JobState] = mapped_column(String(32), default=JobState.QUEUED)
    draft_version_id: Mapped[str | None] = mapped_column(ForeignKey("content_versions.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ProcessedUpdate(Base):
    """Telegram update ids already handled; the primary key makes redelivery a no-op."""

    __tablename__ = "processed_updates"

    update_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_hit_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_miss_tokens: Mapped[int | None] = mapped_column(Integer)
    # NULL means unknown (usage not reported), never assumed zero.
    cost_usd: Mapped[Decimal | None] = mapped_column(DecimalText)
    pricing_version: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
