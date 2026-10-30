"""Public HTTP API. Read-only; it never mutates content and never exposes unpublished data
except through a signed, expiring, job-bound preview token."""

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from muda.domain.job_state import JobState
from muda.domain.site import Change, SiteContent, diff_content
from muda.observability import correlation_id
from muda.persistence import jobs, publishing
from muda.persistence.models import Job, utcnow
from muda.preview_token import verify_preview_token
from muda.settings import Settings

logger = logging.getLogger("muda.api")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class SiteResponse(BaseModel):
    version_id: str
    content: SiteContent


class PreviewResponse(BaseModel):
    job_id: str
    published: SiteContent
    draft: SiteContent
    changes: list[Change]
    expires_at: datetime


def create_app(engine: Engine, settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        with Session(engine) as s, jobs.write_tx(s):
            publishing.seed_initial(s)
        yield

    app = FastAPI(title="Muda", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid4().hex
        token = correlation_id.set(request_id)
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((time.monotonic() - started) * 1000, 1)
            # The path is deliberately omitted: preview tokens travel in it.
            logger.info(
                "request",
                extra={"method": request.method, "duration_ms": duration_ms},
            )
            correlation_id.reset(token)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["x-frame-options"] = "DENY"
        return response

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", exc_info=exc)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.get("/healthz")
    def healthz() -> Response:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            logger.exception("health_check_failed")
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/api/site")
    def get_site(response: Response) -> SiteResponse:
        with Session(engine) as s:
            version_id, content = publishing.get_published(s)
        response.headers["cache-control"] = "no-cache"
        return SiteResponse(version_id=version_id, content=content)

    @app.get("/api/preview/{token}")
    def get_preview(token: str, response: Response) -> PreviewResponse:
        claims = verify_preview_token(
            settings.preview_secret.get_secret_value(), token, now=utcnow()
        )
        not_found = HTTPException(status_code=404, detail="not found")
        if claims is None:
            raise not_found
        with Session(engine) as s:
            job = s.get(Job, claims.job_id)
            # Valid only while this exact draft is still awaiting a decision.
            if (
                job is None
                or job.state != JobState.AWAITING_APPROVAL
                or job.draft_version_id != claims.draft_version_id
            ):
                raise not_found
            _, published = publishing.get_published(s)
            draft = publishing.get_version_content(s, claims.draft_version_id)
        response.headers["cache-control"] = "no-store"
        response.headers["x-robots-tag"] = "noindex"
        return PreviewResponse(
            job_id=claims.job_id,
            published=published,
            draft=draft,
            changes=diff_content(published, draft),
            expires_at=claims.expires_at,
        )

    return app
