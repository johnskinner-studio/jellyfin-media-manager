from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import datetime

import uvicorn

from .config import get_settings
from .metadata.tmdb import TMDBClient
from .metadata.tvdb import TVDBClient
from .scanner import scan_library
from .watcher import start_observer
from .web.app import app as fastapi_app
from .web.logging_handler import LogCaptureHandler
from .web.state import ActivityEntry, init_state, get_state


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def _start_uvicorn() -> None:
    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=4000,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Prevent uvicorn from registering its own signal handlers — main() owns those
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    server.run()


def main() -> None:
    # 1. Init shared state before anything else
    state = init_state()

    settings = get_settings()
    _configure_logging(settings.log_level)

    # 2. Install log capture so every log line feeds the web UI ring buffer
    log_handler = LogCaptureHandler(state)
    log_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(log_handler)

    log = logging.getLogger(__name__)
    log.info("Jellyfin Media Manager starting up")
    if settings.dry_run:
        log.info("DRY RUN mode enabled — no files will be moved or deleted")

    tmdb = TMDBClient(settings.tmdb_api_key)
    tvdb = TVDBClient(settings.tvdb_api_key) if settings.tvdb_api_key.strip() else None
    if tvdb:
        log.info("TVDB client enabled as fallback for episode titles")

    # 3. Instrument watcher's process() so the web UI receives structured activity
    import src.watcher as _watcher_module  # noqa: PLC0415

    _orig_process = _watcher_module.process

    def _instrumented_process(candidate, settings, tmdb, tvdb, library_type):
        report = _orig_process(candidate, settings, tmdb, tvdb, library_type)
        get_state().add_activity(ActivityEntry.from_report(report, library_type))
        return report

    _watcher_module.process = _instrumented_process

    # 4. Startup scan — update scan_status and feed activity
    if settings.scan_on_start:
        log.info("Running initial library scan...")
        for lib_path, lib_type in [
            (settings.movies_library_path, "movies"),
            (settings.tv_library_path, "tv"),
        ]:
            state.scan_status[lib_type].running = True
            try:
                reports = scan_library(lib_path, lib_type, settings, tmdb, tvdb)  # type: ignore[arg-type]
                state.scan_status[lib_type].last_run = datetime.utcnow()
                for r in reports:
                    state.add_activity(ActivityEntry.from_report(r, lib_type))
                moved = sum(1 for r in reports if r.result.value == "moved")
                log.info(
                    "Scan complete for %s: %d processed, %d moved",
                    lib_path,
                    len(reports),
                    moved,
                )
            except Exception as exc:
                state.scan_status[lib_type].last_error = str(exc)
                log.exception("Startup scan failed for %s", lib_path)
            finally:
                state.scan_status[lib_type].running = False

    # 5. Start filesystem watcher
    observer, worker = start_observer(settings, tmdb, tvdb)  # type: ignore[arg-type]

    # 6. Start FastAPI / uvicorn in a daemon background thread
    uvicorn_thread = threading.Thread(
        target=_start_uvicorn,
        daemon=True,
        name="uvicorn",
    )
    uvicorn_thread.start()
    log.info("Web UI available at http://0.0.0.0:4000")

    # 7. Block until SIGTERM or SIGINT
    shutdown_event = threading.Event()

    def _handle_signal(sig: int, _frame: object) -> None:
        log.info("Received signal %s, shutting down...", signal.Signals(sig).name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log.info("Watching for new media. Press Ctrl+C to stop.")
    shutdown_event.wait()

    log.info("Stopping watcher...")
    worker.stop()
    observer.stop()
    observer.join()
    tmdb.close()
    if tvdb:
        tvdb.close()
    log.info("Shutdown complete.")


if __name__ == "__main__":
    main()
