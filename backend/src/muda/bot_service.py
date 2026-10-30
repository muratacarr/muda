"""Telegram-independent bot logic. Every method authorizes by numeric user id first and
returns None (silence) for anyone else."""

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from muda.domain.job_state import JobState
from muda.persistence import jobs, publishing
from muda.persistence.models import AiUsage, Job, utcnow
from muda.rate_limit import SlidingWindowLimiter
from muda.settings import Settings
from muda.worker import CancelSignals

logger = logging.getLogger("muda.bot")
_CALLBACK = re.compile(r"^([ar]):([0-9a-f]{32}):([0-9a-f]{16})$")

ERROR_TEXT = {
    "AI_TIMEOUT": "The AI service did not answer in time. Nothing was changed. Please try again.",
    "AI_UNAVAILABLE": "The AI service is unavailable right now. Nothing was changed.",
    "AI_BAD_RESPONSE": "The AI returned an unusable answer. Nothing was changed.",
    "AI_AUTH": "The AI service rejected our credentials. Nothing was changed.",
    "AI_INSUFFICIENT_BALANCE": "The AI account has no balance left. Nothing was changed.",
    "AI_INPUT_TOO_LARGE": "That request is too large. Nothing was changed.",
    "AI_CALL_LIMIT": "The AI call limit for this request was reached. Nothing was changed.",
    "AI_JOB_BUDGET": "This request would exceed the per-request AI budget. Nothing was changed.",
    "AI_MONTHLY_BUDGET": "The monthly AI budget is used up. Nothing was changed.",
    "AI_PRICING_UNKNOWN": "AI pricing is not configured. Nothing was changed.",
    "INVALID_PROPOSAL": "I could not turn that into a safe, supported change. Nothing was changed.",
    "UNSUPPORTED_REQUEST": "That request is not supported yet. I can change menu prices "
    "and the site design (theme, colors, alignment, heading size, card roundness, columns).",
    "UNKNOWN_PRODUCT": "I could not find that product on the menu. Nothing was changed.",
    "INTERRUPTED": "The request was interrupted by a restart. Nothing was changed.",
    "STALE_BASE": "The site changed while this was pending. Nothing was changed.",
    "INTERNAL_ERROR": "Something went wrong on our side. Nothing was changed.",
}


@dataclass(frozen=True)
class Reply:
    text: str
    # (label, callback_data) pairs rendered as inline keyboard buttons
    buttons: tuple[tuple[str, str], ...] = ()


def approval_buttons(job_id: str, draft_version_id: str) -> tuple[tuple[str, str], ...]:
    """callback_data is capped at 64 bytes, so only a draft-id prefix travels in the button;
    the full id is checked server-side against the job."""
    suffix = f"{job_id}:{draft_version_id[:16]}"
    return (("Approve", f"a:{suffix}"), ("Reject", f"r:{suffix}"))


class BotService:
    def __init__(self, engine: Engine, settings: Settings, signals: CancelSignals) -> None:
        self._engine = engine
        self._settings = settings
        self._signals = signals
        self._limiter = SlidingWindowLimiter(settings.rate_limit_messages_per_minute, 60)

    def _authorized(self, user_id: int) -> bool:
        allowed = self._settings.telegram_allowed_user_id
        return allowed is not None and user_id == allowed  # fail closed when unset

    # ---- commands ---------------------------------------------------------------------

    def start(self, user_id: int) -> Reply | None:
        if not self._authorized(user_id):
            return None
        return Reply(
            "Welcome to Muda. Tell me what to change on the Luna Cafe website, for example:\n"
            "• Change the Latte price to 150 TL.\n"
            "• Make the website dark and center the hero title.\n\n"
            "Commands: /status /cancel /history"
        )

    def status(self, user_id: int) -> Reply | None:
        if not self._authorized(user_id):
            return None
        with Session(self._engine) as s:
            job = jobs.get_active_job(s)
            if job is None:
                return Reply("No active job.")
            lines = [f"Job {job.id[:8]}: {job.state}", f"Request: {job.prompt[:120]}"]
            if job.state == JobState.AWAITING_APPROVAL:
                lines.append("Waiting for your approval. Use the Approve/Reject buttons.")
            return Reply("\n".join(lines))

    def history(self, user_id: int) -> Reply | None:
        if not self._authorized(user_id):
            return None
        with Session(self._engine) as s:
            recent = jobs.list_recent(s, 5)
        if not recent:
            return Reply("No jobs yet.")
        return Reply(
            "\n".join(
                f"{j.id[:8]} {j.state}{f' ({j.error_code})' if j.error_code else ''} - "
                f"{j.prompt[:40]}"
                for j in recent
            )
        )

    def cancel(self, user_id: int) -> Reply | None:
        if not self._authorized(user_id):
            return None
        with Session(self._engine) as s, jobs.write_tx(s):
            job = jobs.get_active_job(s)
            if job is None:
                return Reply("No active job to cancel.")
            job_id = job.id
            result = jobs.request_cancel(s, job_id)
        if result == "not_cancellable":
            return Reply("The change is being published and can no longer be cancelled.")
        self._signals.set(job_id)  # wake the worker; the persisted flag is the source of truth
        if result == "already_requested":
            return Reply("Cancellation was already requested.")
        return Reply("Cancellation requested. Nothing will be published.")

    # ---- natural language ---------------------------------------------------------------

    def handle_text(self, update_id: int, user_id: int, text: str) -> Reply | None:
        if not self._authorized(user_id):
            logger.warning("unauthorized_message", extra={"update_id": update_id})
            return None
        if not self._limiter.allow():
            return Reply("Too many messages. Please slow down and try again in a minute.")
        prompt = text.strip()
        if not prompt:
            return Reply("Please describe the change you want.")
        if len(prompt) > self._settings.max_prompt_chars:
            return Reply(
                "That message is too long. "
                f"The limit is {self._settings.max_prompt_chars} characters."
            )
        if self._ai_rate_limited():
            return Reply("Please wait a minute before sending another request.")
        with Session(self._engine) as s, jobs.write_tx(s):
            result = jobs.create_job(
                s, update_id=update_id, telegram_user_id=user_id, prompt=prompt
            )
        if result.status == "duplicate":
            return None  # Telegram redelivery: stay silent, no second job, no second AI charge
        if result.status == "active_job_exists":
            return Reply("A request is already in progress. Use /status to check it or /cancel.")
        job_id = result.job_id or ""
        return Reply(
            f"Request received (job {job_id[:8]}). I'll message you when the preview is ready."
        )

    def _ai_rate_limited(self) -> bool:
        since = utcnow() - timedelta(minutes=1)
        with Session(self._engine) as s:
            recent = s.scalar(
                select(func.count()).select_from(AiUsage).where(AiUsage.created_at >= since)
            )
        return (recent or 0) >= self._settings.rate_limit_ai_requests_per_minute

    # ---- approval buttons ---------------------------------------------------------------

    def handle_callback(self, user_id: int, data: str) -> Reply | None:
        if not self._authorized(user_id):
            return None
        match = _CALLBACK.match(data)
        if match is None:
            return None
        action, job_id, draft_prefix = match.groups()
        with Session(self._engine) as s, jobs.write_tx(s):
            job = s.get(Job, job_id)
            if job is None:
                return None
            draft_id = job.draft_version_id
            if draft_id is None or not draft_id.startswith(draft_prefix):
                return Reply("This approval is no longer valid.")
            decide = publishing.approve if action == "a" else publishing.reject
            result = decide(s, job_id, draft_id, user_id)
        logger.info("decision", extra={"job_id": job_id, "result": result})
        return Reply(
            {
                "published": "Approved and published. The live site is updated.",
                "rejected": "Rejected. The live site is unchanged.",
                "already_processed": "This request was already handled.",
                "stale_version": "This approval is no longer valid.",
                "cancelled": "This request was cancelled. Nothing was published.",
                "unauthorized": "Not allowed.",
            }[result]
        )


def failure_text(error_code: str | None) -> str:
    return ERROR_TEXT.get(error_code or "", "The request failed. Nothing was changed.")


__all__ = ["BotService", "Reply", "approval_buttons", "failure_text"]
