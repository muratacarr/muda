from datetime import UTC, datetime, timedelta

from muda.preview_token import create_preview_token, verify_preview_token

SECRET = "s" * 40
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
TTL = timedelta(minutes=30)


def test_roundtrip() -> None:
    token = create_preview_token(SECRET, "job1", "draft1", TTL, now=NOW)
    claims = verify_preview_token(SECRET, token, now=NOW + timedelta(minutes=29))
    assert claims is not None
    assert (claims.job_id, claims.draft_version_id) == ("job1", "draft1")
    assert claims.expires_at == NOW + TTL


def test_expired_token_is_rejected() -> None:
    token = create_preview_token(SECRET, "job1", "draft1", TTL, now=NOW)
    assert verify_preview_token(SECRET, token, now=NOW + TTL + timedelta(seconds=1)) is None


def test_wrong_secret_is_rejected() -> None:
    token = create_preview_token(SECRET, "job1", "draft1", TTL, now=NOW)
    assert verify_preview_token("x" * 40, token, now=NOW) is None


def test_tampered_payload_is_rejected() -> None:
    token = create_preview_token(SECRET, "job1", "draft1", TTL, now=NOW)
    other = create_preview_token(SECRET, "job2", "draft1", TTL, now=NOW)
    forged = other.split(".")[0] + "." + token.split(".")[1]
    assert verify_preview_token(SECRET, forged, now=NOW) is None


def test_garbage_is_rejected() -> None:
    for junk in ("", "abc", "a.b", "....", "e30.e30", "💥", "a" * 5000):
        assert verify_preview_token(SECRET, junk, now=NOW) is None
