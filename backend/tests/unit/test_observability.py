import json
import logging

from muda.observability import JsonFormatter, correlation_id


def _record(msg: str, **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("muda.test", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_output_is_json_with_correlation_id_and_extras() -> None:
    token = correlation_id.set("abc123")
    try:
        out = json.loads(JsonFormatter().format(_record("hello", job_id="j1")))
    finally:
        correlation_id.reset(token)
    assert out["message"] == "hello"
    assert out["level"] == "INFO"
    assert out["correlation_id"] == "abc123"
    assert out["job_id"] == "j1"
    assert "timestamp" in out


def test_secrets_are_redacted_everywhere() -> None:
    fmt = JsonFormatter(redact=["sk-super-secret"])
    line = fmt.format(_record("key is sk-super-secret", detail="x sk-super-secret y"))
    assert "sk-super-secret" not in line
    assert "[REDACTED]" in line


def test_exception_is_included_but_redacted() -> None:
    fmt = JsonFormatter(redact=["tok-123"])
    try:
        raise RuntimeError("failed with tok-123")
    except RuntimeError:
        import sys

        rec = _record("boom")
        rec.exc_info = sys.exc_info()
    line = fmt.format(rec)
    assert "RuntimeError" in line
    assert "tok-123" not in line


def test_empty_redaction_values_are_ignored() -> None:
    line = JsonFormatter(redact=["", "x"]).format(_record("hello world"))
    assert "hello world" in line
