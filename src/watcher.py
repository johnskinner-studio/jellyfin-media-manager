from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import (
    DirCreatedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from .processor import ProcessReport, process

if TYPE_CHECKING:
    from .config import Settings
    from .metadata.tmdb import TMDBClient
    from .metadata.tvdb import TVDBClient

log = logging.getLogger(__name__)


class MediaEventHandler(FileSystemEventHandler):
    """Enqueues paths for processing when files/dirs appear or are moved in."""

    def __init__(self, work_queue: queue.Queue[str], library_type: str) -> None:
        super().__init__()
        self._queue = work_queue
        self._library_type = library_type

    def on_created(self, event: FileCreatedEvent | DirCreatedEvent) -> None:
        self._queue.put(event.src_path)

    def on_moved(self, event: FileMovedEvent | DirMovedEvent) -> None:
        # A downloader may move a completed file into the watch dir
        self._queue.put(event.dest_path)


class DebouncedWorker(threading.Thread):
    """
    Reads paths from the queue and only processes them after SETTLE_DELAY
    seconds of no new events for that path.  This prevents acting on a file
    mid-download.
    """

    def __init__(
        self,
        work_queue: queue.Queue[str],
        settings: "Settings",
        tmdb: "TMDBClient",
        tvdb: "TVDBClient | None",
        tv_library_path: Path,
        movies_library_path: Path,
    ) -> None:
        super().__init__(daemon=True, name="DebouncedWorker")
        self._queue = work_queue
        self._settings = settings
        self._tmdb = tmdb
        self._tvdb = tvdb
        self._tv_path = tv_library_path
        self._movies_path = movies_library_path
        self._pending: dict[str, float] = {}  # path → scheduled_fire_time
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        settle = self._settings.settle_delay
        while not self._stop_event.is_set():
            # Drain the queue, updating pending timers
            try:
                while True:
                    path = self._queue.get_nowait()
                    with self._lock:
                        self._pending[path] = time.monotonic() + settle
            except queue.Empty:
                pass

            self._flush_ready()
            time.sleep(1.0)

    def _flush_ready(self) -> None:
        now = time.monotonic()
        with self._lock:
            ready = [p for p, t in self._pending.items() if t <= now]
            for p in ready:
                del self._pending[p]

        for path_str in ready:
            path = Path(path_str)
            library_type = self._detect_library_type(path)
            if library_type is None:
                log.debug("Ignoring event for path outside known libraries: %s", path)
                continue
            log.info("Processing new media: %s", path)
            report = process(path, self._settings, self._tmdb, self._tvdb, library_type)
            _log_report(report)

    def _detect_library_type(self, path: Path) -> str | None:
        try:
            path.relative_to(self._movies_path)
            return "movies"
        except ValueError:
            pass
        try:
            path.relative_to(self._tv_path)
            return "tv"
        except ValueError:
            pass
        return None


def start_observer(
    settings: "Settings",
    tmdb: "TMDBClient",
    tvdb: "TVDBClient | None",
) -> tuple[Observer, DebouncedWorker]:
    """Create, configure, and start the filesystem observer + debounce worker."""
    work_queue: queue.Queue[str] = queue.Queue()
    observer = Observer()

    for lib_path, lib_type in [
        (settings.movies_library_path, "movies"),
        (settings.tv_library_path, "tv"),
    ]:
        if lib_path.exists():
            handler = MediaEventHandler(work_queue, lib_type)
            observer.schedule(handler, str(lib_path), recursive=True)
            log.info("Watching %s (%s)", lib_path, lib_type)
        else:
            log.warning("Library path not found, not watching: %s", lib_path)

    worker = DebouncedWorker(
        work_queue,
        settings,
        tmdb,
        tvdb,
        settings.tv_library_path,
        settings.movies_library_path,
    )

    worker.start()
    observer.start()
    return observer, worker


def _log_report(report: ProcessReport) -> None:
    from .processor import ProcessResult

    if report.result == ProcessResult.MOVED:
        log.info("Organized: %s → %s", report.source, report.destination)
    elif report.result == ProcessResult.DRY_RUN:
        log.info("[DRY RUN] Would move: %s → %s", report.source, report.destination)
    elif report.result in (
        ProcessResult.ERROR_NO_METADATA,
        ProcessResult.ERROR_CONFLICT,
        ProcessResult.ERROR_EXCEPTION,
    ):
        log.warning(
            "Failed [%s]: %s — %s", report.result.value, report.source, report.message
        )
    else:
        log.debug("[%s] %s", report.result.value, report.source)
