"""Request IDs and JSON logs.

Every request gets an ID: the one nginx sends in the X-Request-ID header when it looks valid, otherwise a new
one. The ID is sent back in the X-Request-ID response header and added to every log line written while the
request is handled, so one request can be followed through the logs:

    docker compose logs backend | Select-String <request id>

Every log line is one JSON object (easy to search and to load into a log tool later).
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_VALID_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")  # anything else (too long, odd characters) is replaced

# Docker calls these every few seconds: log them only at DEBUG level, so they do not fill the logs.
QUIET_PATHS = {"/api/health/", "/api/ready/"}

logger = logging.getLogger("requests")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def current_request_id() -> str:
    """The ID of the request being handled by this thread ("" outside a request)."""
    return _request_id.get()


class RequestIdMiddleware:
    """Gives every request an ID and writes one JSON log line per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _VALID_ID.match(incoming) else new_request_id()
        request.request_id = request_id
        _request_id.set(request_id)

        start = time.monotonic()
        response = self.get_response(request)
        response["X-Request-ID"] = request_id

        user = getattr(request, "user", None)
        level = logging.DEBUG if request.path in QUIET_PATHS else logging.INFO
        # For a streamed answer this is the time until streaming starts; chat/services.py logs the full time.
        logger.log(
            level,
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "user_id": user.pk if user is not None and user.is_authenticated else None,
                    "streaming": response.streaming,
                }
            },
        )
        return response


class RequestIdFilter(logging.Filter):
    """Adds the current request ID to every log record (unless the code already set one)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", ""):
            record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Extra values go in `extra={"fields": {...}}`."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            data["request_id"] = request_id
        data.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            data["error"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)
