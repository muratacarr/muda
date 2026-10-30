"""Stateless, signed, expiring preview tokens bound to one job and one draft version."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

MAX_TOKEN_LEN = 512


@dataclass(frozen=True)
class PreviewClaims:
    job_id: str
    draft_version_id: str
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode("ascii"), hashlib.sha256).hexdigest()


def create_preview_token(
    secret: str, job_id: str, draft_version_id: str, ttl: timedelta, *, now: datetime
) -> str:
    expires = int((now + ttl).timestamp())
    payload = _b64(
        json.dumps(
            {"j": job_id, "d": draft_version_id, "e": expires}, separators=(",", ":")
        ).encode()
    )
    return f"{payload}.{_sign(secret, payload)}"


def verify_preview_token(secret: str, token: str, *, now: datetime) -> PreviewClaims | None:
    """Return the claims for a genuine, unexpired token, otherwise None (never raises)."""
    if not token.isascii() or len(token) > MAX_TOKEN_LEN:
        return None
    payload, sep, signature = token.partition(".")
    if not sep or not hmac.compare_digest(_sign(secret, payload), signature):
        return None
    try:
        data = json.loads(_unb64(payload))
        job_id, draft_id, expires = str(data["j"]), str(data["d"]), int(data["e"])
    except (ValueError, KeyError, TypeError):
        return None
    expires_at = datetime.fromtimestamp(expires, tz=UTC)
    if now >= expires_at:
        return None
    return PreviewClaims(job_id, draft_id, expires_at)
