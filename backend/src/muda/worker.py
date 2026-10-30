"""Persistent job worker: DB-driven loop, cancellation checkpoints, AI call, draft creation."""

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from muda.ai.budget import BudgetExceededError, BudgetLimits, ensure_can_call, record_usage
from muda.ai.pricing import PricingTable
from muda.ai.prompt import build_messages, site_summary
from muda.ai.provider import AIProvider, AIProviderError
from muda.ai.types import AIChangeRequest, TokenUsage
from muda.domain.job_state import JobState
from muda.domain.operations import UpdateDesign, UpdateMenuPrice, parse_operation
from muda.observability import correlation_id
from muda.persistence import jobs, publishing
from muda.persistence.models import utcnow

logger = logging.getLogger("muda.worker")

NotificationKind = Literal["awaiting_approval", "failed", "cancelled"]


@dataclass(frozen=True)
class JobNotification:
    job_id: str
    kind: NotificationKind
    error_code: str | None = None
    draft_version_id: str | None = None


class Notifier(Protocol):
    async def notify(self, notification: JobNotification) -> None: ...


class CancelSignals:
    """Local, in-process cancellation signal (like a CancellationToken).

    The persisted `cancel_requested` flag is the source of truth; this only wakes
    the worker early so it can stop waiting on a slow AI call.
    """

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def event(self, job_id: str) -> asyncio.Event:
        return self._events.setdefault(job_id, asyncio.Event())

    def set(self, job_id: str) -> None:
        self.event(job_id).set()

    def discard(self, job_id: str) -> None:
        self._events.pop(job_id, None)


class JobProcessor:
    def __init__(  # noqa: PLR0913
        self,
        engine: Engine,
        provider: AIProvider,
        *,
        pricing: PricingTable,
        limits: BudgetLimits,
        model: str,
        max_output_tokens: int,
        provider_name: str = "deepseek",
        notifier: Notifier | None = None,
        signals: CancelSignals | None = None,
        clock: Callable[[], datetime] = utcnow,
        approval_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        self._engine = engine
        self._provider = provider
        self._pricing = pricing
        self._limits = limits
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._provider_name = provider_name
        self._notifier = notifier
        self._signals = signals or CancelSignals()
        self._clock = clock
        self._approval_ttl = approval_ttl

    # ---- loop -------------------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event, *, poll_s: float = 0.5) -> None:
        report = await asyncio.to_thread(self._recover)
        logger.info("worker_started", extra={"recovery": report})
        while not stop.is_set():
            try:
                did_work = await self.run_once()
                await asyncio.to_thread(self._expire)
            except Exception:
                logger.exception("worker_loop_error")
                did_work = False
            if not did_work:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), poll_s)

    async def run_once(self) -> bool:
        """Finalize pending cancellations, then claim and process at most one job."""
        for cancelled_id in await asyncio.to_thread(self._finalize_cancelling):
            await self._notify(JobNotification(cancelled_id, "cancelled"))
        job_id = await asyncio.to_thread(self._claim)
        if job_id is None:
            return False
        token = correlation_id.set(job_id)  # every log line and AI request traces to the job
        try:
            await self._process(job_id)
        except Exception:
            logger.exception("job_internal_error", extra={"job_id": job_id})
            await self._finish_failed(job_id, "INTERNAL_ERROR")
        finally:
            self._signals.discard(job_id)
            correlation_id.reset(token)
        return True

    # ---- processing -------------------------------------------------------------------

    async def _process(self, job_id: str) -> None:
        loaded = await asyncio.to_thread(self._load, job_id)
        if loaded is None:  # cancellation checkpoint 1: before any paid work
            await self._finish_cancelled(job_id)
            return
        prompt, summary = loaded
        request = AIChangeRequest(prompt=prompt, site_summary=summary)

        est_input = sum(len(m["content"]) for m in build_messages(request))  # pessimistic
        try:
            await asyncio.to_thread(self._reserve, job_id, est_input)
        except BudgetExceededError as exc:
            await self._finish_failed(job_id, exc.code)
            return

        payload = await self._call_provider(job_id, request)
        if payload is None:
            return

        # cancellation checkpoint 3 happens inside the draft transaction (race-free)
        try:
            op = parse_operation(payload)
        except ValidationError:
            code = (
                "UNSUPPORTED_REQUEST"
                if payload.get("operation") == "unsupported"
                else "INVALID_PROPOSAL"
            )
            await self._finish_failed(job_id, code)
            return
        outcome, draft_id = await asyncio.to_thread(self._create_draft, job_id, op)
        if outcome == "awaiting_approval":
            await self._notify(
                JobNotification(job_id, "awaiting_approval", draft_version_id=draft_id)
            )
        elif outcome == "cancelled":
            await self._notify(JobNotification(job_id, "cancelled"))
        else:
            await self._notify(JobNotification(job_id, "failed", error_code=outcome))

    async def _call_provider(
        self, job_id: str, request: AIChangeRequest
    ) -> dict[str, object] | None:
        """Run the AI call, racing it against the local cancel signal. None = job already ended."""
        started = time.monotonic()
        call = asyncio.create_task(self._provider.generate_change(request))
        cancelled = asyncio.create_task(self._signals.event(job_id).wait())
        try:
            await asyncio.wait({call, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancelled.cancel()
        latency_ms = int((time.monotonic() - started) * 1000)

        if not call.done():
            call.cancel()
            with suppress(asyncio.CancelledError, AIProviderError):
                await call
            # Provider may still bill for tokens already consumed: cost stays unknown.
            await asyncio.to_thread(self._record, job_id, TokenUsage(), latency_ms, "cancelled")
            await self._finish_cancelled(job_id)
            return None
        try:
            result = call.result()
        except AIProviderError as exc:
            await asyncio.to_thread(
                self._record, job_id, exc.usage or TokenUsage(), latency_ms, exc.code
            )
            await self._finish_failed(job_id, exc.code)
            return None
        await asyncio.to_thread(self._record, job_id, result.usage, result.latency_ms, "ok")
        return result.payload

    # ---- database steps (blocking; always run via asyncio.to_thread) ---------------------

    def _recover(self) -> dict[str, int]:
        with Session(self._engine) as s, jobs.write_tx(s):
            return publishing.recover_interrupted(s)

    def _expire(self) -> int:
        with Session(self._engine) as s, jobs.write_tx(s):
            return publishing.expire_stale(s, self._approval_ttl, now=self._clock())

    def _finalize_cancelling(self) -> list[str]:
        with Session(self._engine) as s, jobs.write_tx(s):
            return publishing.finalize_cancelling(s)

    def _claim(self) -> str | None:
        with Session(self._engine) as s, jobs.write_tx(s):
            return publishing.claim_next_job(s)

    def _load(self, job_id: str) -> tuple[str, str] | None:
        with Session(self._engine) as s:
            job = jobs.get_job(s, job_id)
            if job.cancel_requested or job.state == JobState.CANCELLING:
                return None
            return job.prompt, site_summary(publishing.get_published(s)[1])

    def _reserve(self, job_id: str, est_input_tokens: int) -> None:
        with Session(self._engine) as s, jobs.write_tx(s):
            ensure_can_call(
                s,
                job_id=job_id,
                limits=self._limits,
                pricing=self._pricing,
                model=self._model,
                est_input_tokens=est_input_tokens,
                max_output_tokens=self._max_output_tokens,
                now=self._clock(),
            )

    def _record(self, job_id: str, usage: TokenUsage, latency_ms: int, status: str) -> None:
        with Session(self._engine) as s, jobs.write_tx(s):
            record_usage(
                s,
                job_id=job_id,
                provider=self._provider_name,
                model=self._model,
                usage=usage,
                pricing=self._pricing,
                latency_ms=latency_ms,
                status=status,
                now=self._clock(),
            )

    def _create_draft(
        self, job_id: str, op: UpdateMenuPrice | UpdateDesign
    ) -> tuple[str, str | None]:
        with Session(self._engine) as s, jobs.write_tx(s):
            try:
                draft_id = publishing.create_draft(s, job_id, op)
            except publishing.JobCancelledError:
                publishing.finish_cancellation(s, job_id)
                return "cancelled", None
            except publishing.InvalidProposalError:
                self._fail_in_tx(s, job_id, "UNKNOWN_PRODUCT")
                return "UNKNOWN_PRODUCT", None
            return "awaiting_approval", draft_id

    @staticmethod
    def _fail_in_tx(s: Session, job_id: str, code: str) -> str:
        """Fail the job, unless a cancel was requested meanwhile (then it ends as CANCELLED)."""
        if jobs.get_job(s, job_id).state == JobState.CANCELLING:
            publishing.finish_cancellation(s, job_id)
            return "cancelled"
        jobs.transition(s, job_id, JobState.FAILED, error_code=code)
        return "failed"

    def _fail(self, job_id: str, code: str) -> str:
        with Session(self._engine) as s, jobs.write_tx(s):
            return self._fail_in_tx(s, job_id, code)

    def _cancel(self, job_id: str) -> None:
        with Session(self._engine) as s, jobs.write_tx(s):
            publishing.finish_cancellation(s, job_id)

    async def _finish_failed(self, job_id: str, code: str) -> None:
        outcome = await asyncio.to_thread(self._fail, job_id, code)
        if outcome == "cancelled":
            await self._notify(JobNotification(job_id, "cancelled"))
        else:
            await self._notify(JobNotification(job_id, "failed", error_code=code))

    async def _finish_cancelled(self, job_id: str) -> None:
        await asyncio.to_thread(self._cancel, job_id)
        await self._notify(JobNotification(job_id, "cancelled"))

    async def _notify(self, notification: JobNotification) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.notify(notification)
        except Exception:
            # A delivery problem must never change job state or crash the worker.
            logger.exception("notification_failed", extra={"job_id": notification.job_id})
