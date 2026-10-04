from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from fastapi import WebSocket
    from ..processor import ProcessReport


def pending_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


@dataclass
class LogEntry:
    ts: str
    level: str
    logger: str
    message: str


@dataclass
class ActivityEntry:
    ts: str
    source: str
    result: str
    destination: str | None
    message: str
    library_type: str

    @classmethod
    def from_report(cls, report: "ProcessReport", lib_type: str) -> "ActivityEntry":
        return cls(
            ts=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            source=str(report.source),
            result=report.result.value,
            destination=str(report.destination) if report.destination else None,
            message=report.message,
            library_type=lib_type,
        )


@dataclass
class PendingItem:
    id: str
    path: str
    library_type: str
    parsed_title: str
    parsed_year: int | None
    first_seen: str
    last_error: str = ""


@dataclass
class ScanStatus:
    running: bool = False
    last_run: datetime | None = None
    last_error: str | None = None


class AppState:
    def __init__(self) -> None:
        self.activity: deque[ActivityEntry] = deque(maxlen=500)
        self.log_buffer: deque[LogEntry] = deque(maxlen=2000)
        self.pending: dict[str, PendingItem] = {}
        self.scan_status: dict[str, ScanStatus] = {
            "movies": ScanStatus(),
            "tv": ScanStatus(),
        }
        self.startup_time: datetime = datetime.utcnow()
        self._lock = threading.Lock()
        self._ws_clients: set = set()
        self._ws_lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._scan_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="api-scan")
        self._scan_futures: dict[str, Future | None] = {"movies": None, "tv": None}
        # Library counts require walking the whole tree, so they are cached
        # (key → (computed_at, value)) and invalidated when files are moved.
        self._count_cache: dict[str, tuple[float, int]] = {}
        self._count_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Sync methods (called from any thread)
    # ------------------------------------------------------------------

    def add_activity(self, entry: ActivityEntry) -> None:
        with self._lock:
            self.activity.appendleft(entry)
        if entry.result == "moved":
            self.invalidate_counts()

    def invalidate_counts(self) -> None:
        with self._count_lock:
            self._count_cache.clear()

    def cached_count(self, key: str, compute: Callable[[], int], ttl: float = 300.0) -> int:
        """Return a cached library count, recomputing at most once per ttl.
        The lock is held while computing so concurrent callers share one walk
        instead of each hammering the disk."""
        with self._count_lock:
            hit = self._count_cache.get(key)
            now = time.monotonic()
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
            value = compute()
            self._count_cache[key] = (time.monotonic(), value)
            return value

    def add_log(self, entry: LogEntry) -> None:
        with self._lock:
            self.log_buffer.append(entry)

    def broadcast_log(self, entry: LogEntry) -> None:
        if self._loop is None:
            return
        payload = json.dumps({"type": "log_line", "entries": [asdict(entry)]})
        asyncio.run_coroutine_threadsafe(self._async_broadcast(payload), self._loop)

    def register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._ws_lock = asyncio.Lock()

    def start_scan(self, lib_type: str, fn: Callable) -> bool:
        fut = self._scan_futures.get(lib_type)
        if fut is not None and not fut.done():
            return False
        self._scan_futures[lib_type] = self._scan_executor.submit(fn)
        return True

    # ------------------------------------------------------------------
    # Async methods (called from FastAPI event loop)
    # ------------------------------------------------------------------

    async def ws_connect(self, ws: "WebSocket") -> None:
        assert self._ws_lock is not None
        async with self._ws_lock:
            self._ws_clients.add(ws)

    async def ws_disconnect(self, ws: "WebSocket") -> None:
        assert self._ws_lock is not None
        async with self._ws_lock:
            self._ws_clients.discard(ws)

    async def _async_broadcast(self, payload: str) -> None:
        if not self._ws_clients:
            return
        assert self._ws_lock is not None
        async with self._ws_lock:
            dead: set = set()
            for ws in list(self._ws_clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            self._ws_clients -= dead

    def get_activity_list(self) -> list[ActivityEntry]:
        with self._lock:
            return list(self.activity)

    def add_pending(self, item: PendingItem) -> None:
        with self._lock:
            self.pending.setdefault(item.id, item)

    def remove_pending(self, item_id: str) -> bool:
        with self._lock:
            return self.pending.pop(item_id, None) is not None

    def get_pending_list(self) -> list[PendingItem]:
        with self._lock:
            return sorted(self.pending.values(), key=lambda x: x.first_seen, reverse=True)

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self.pending)

    def get_log_list(self) -> list[LogEntry]:
        with self._lock:
            return list(self.log_buffer)


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_state: AppState | None = None


def init_state() -> AppState:
    global _state
    _state = AppState()
    return _state


def get_state() -> AppState:
    if _state is None:
        raise RuntimeError("AppState not initialised — call init_state() first")
    return _state
