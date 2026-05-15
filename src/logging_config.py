"""Structured JSON logging with request-ID context support.

Exposes :func:`configure_logging` (called once at startup) and
:data:`request_id_var` (set by the request-ID middleware per request).
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from src.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JSONFormatter(logging.Formatter):
    """Emit a single-line JSON record per log event.

    Every record carries ``timestamp`` (UTC ISO-8601), ``level``, ``logger``,
    and ``message``. When *request_id_var* is set (by the request-ID
    middleware) it is included as ``request_id``. Any extra keyword arguments
    passed to the log call (``logger.info(..., extra={...})``) are folded into
    the JSON object, as is ``exc_info`` when an exception is attached.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info and record.exc_info[0]:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(settings: Settings) -> None:
    """Install the JSON formatter as the sole handler on the root logger.

    Must be called once at application startup before any I/O is performed.

    Args:
        settings: The validated :class:`Settings` instance whose ``log_level``
            controls the root logger threshold.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
