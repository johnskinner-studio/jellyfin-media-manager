from __future__ import annotations

import logging
from datetime import datetime

from .state import AppState, LogEntry


class LogCaptureHandler(logging.Handler):
    """
    Installed on the root logger. Feeds every log record into AppState's
    ring buffer and broadcasts to connected WebSocket clients.
    """

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = LogEntry(
                ts=datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S"),
                level=record.levelname,
                logger=record.name,
                message=self.format(record),
            )
            self._state.add_log(entry)
            self._state.broadcast_log(entry)
        except Exception:
            self.handleError(record)
