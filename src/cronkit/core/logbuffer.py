"""An in-memory tail of the log, for the dashboard to show.

Railway keeps the real logs, but reaching them means leaving the dashboard. This
keeps the last few hundred records in a ring buffer so "what did that run do?"
is answerable on the page itself.

It is deliberately bounded and deliberately in memory: the daemon has no storage,
and a restart losing its log tail is not worth a database.
"""

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

DEFAULT_CAPACITY = 500

# Chatter from the HTTP client that would otherwise drown out the tool output.
NOISY_LOGGERS = ("httpx", "httpcore", "uvicorn.access")


class LogBuffer(logging.Handler):
    """A logging handler that remembers the most recent records."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        super().__init__()
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        # A failure to record a log line must never break what produced it.
        try:
            self._records.append(
                {
                    "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }
            )
        except Exception:  # pragma: no cover - defensive
            self.handleError(record)

    def records(self, limit: int | None = None, level: str | None = None) -> list[dict[str, Any]]:
        """Most recent last, optionally filtered to one level and above."""
        items = list(self._records)
        if level:
            threshold = logging.getLevelNamesMapping().get(level.upper())
            if threshold is not None:
                items = [
                    r for r in items if (logging.getLevelNamesMapping().get(r["level"], 0) or 0) >= threshold
                ]
        return items[-limit:] if limit else items

    def clear(self) -> None:
        self._records.clear()


def install(capacity: int = DEFAULT_CAPACITY) -> LogBuffer:
    """Attach a buffer to the root logger and quieten the noisy libraries.

    Idempotent: building a second app in one process reuses the buffer already
    attached rather than stacking handlers and double-recording every line.
    Returns the buffer so the server can serve it.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, LogBuffer):
            return handler

    buffer = LogBuffer(capacity)
    root.addHandler(buffer)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return buffer
