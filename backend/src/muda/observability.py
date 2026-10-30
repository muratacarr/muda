"""Structured JSON logging with correlation IDs and secret redaction."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, redact: list[str] | None = None) -> None:
        super().__init__()
        self._redact = [r for r in (redact or []) if r]

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        data.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        line = json.dumps(data, default=str, ensure_ascii=False)
        for secret in self._redact:
            line = line.replace(secret, "[REDACTED]")
        return line


def configure_logging(redact: list[str], level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redact))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO; keep provider traffic out of the logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
