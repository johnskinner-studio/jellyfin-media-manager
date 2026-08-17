from __future__ import annotations

import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ..config import _EDITABLE_FIELDS, get_settings, update_settings
from ..organizer import movies as movie_org
from ..organizer import tv as tv_org
from ..parser import is_video_file, parse_filename
from .state import ActivityEntry, AppState, PendingItem, get_state, pending_id

router = APIRouter(prefix="/api")

_YEAR_FOLDER_RE = re.compile(r".+\(\d{4}\)$")
_SEASON_FOLDER_RE = re.compile(r"^season \d+$", re.IGNORECASE)


# ------------------------------------------------------------------
# Dependency shorthand
# ------------------------------------------------------------------

StateDep = Annotated[AppState, Depends(get_state)]


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

@router.get("/stats")
def stats(state: StateDep) -> dict:
    settings = get_settings()
    movie_count = _count_movies(settings.movies_library_path)
    tv_count = _count_tv_episodes(settings.tv_library_path)
    error_count = sum(
        1 for a in state.get_activity_list()
        if a.result.startswith("error_")
    )
    scan_status = {
        k: {
            "running": v.running,
            "last_run": v.last_run.isoformat() if v.last_run else None,
            "last_error": v.last_error,
        }
        for k, v in state.scan_status.items()
    }
    uptime = int((datetime.utcnow() - state.startup_time).total_seconds())
    return {
        "movie_count": movie_count,
        "tv_episode_count": tv_count,
        "scan_status": scan_status,
        "error_count": error_count,
        "pending_count": state.get_pending_count(),
        "uptime_seconds": uptime,
    }


def _count_movies(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    try:
        for entry in os.scandir(path):
            if entry.is_dir() and _YEAR_FOLDER_RE.match(entry.name):
                count += 1
    except OSError:
        pass
    return count


def _count_tv_episodes(path: Path) -> int:
    if not path.exists():
        return 0
    settings = get_settings()
    count = 0
    try:
        for show in os.scandir(path):
            if not show.is_dir():
                continue
            for season in os.scandir(show.path):
                if not season.is_dir() or not _SEASON_FOLDER_RE.match(season.name):
                    continue
                for ep in os.scandir(season.path):
                    if ep.is_file() and Path(ep.name).suffix.lower() in settings.video_extensions:
                        count += 1
    except OSError:
        pass
    return count


# ------------------------------------------------------------------
# Activity
# ------------------------------------------------------------------

@router.get("/activity")
def activity(
    state: StateDep,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    result: str = Query(default=""),
) -> dict:
    items = state.get_activity_list()
    if result:
        items = [i for i in items if i.result == result]
    total = len(items)
    page = items[offset : offset + limit]
    return {"total": total, "items": [asdict(i) for i in page]}


# ------------------------------------------------------------------
# Manual scan
# ------------------------------------------------------------------

@router.post("/scan/{library_type}", status_code=202)
def trigger_scan(library_type: str, state: StateDep) -> dict:
    if library_type not in ("movies", "tv"):
        raise HTTPException(status_code=400, detail="library_type must be 'movies' or 'tv'")

    settings = get_settings()

    # Import here to avoid circular imports at module load time
    from ..metadata.tmdb import TMDBClient
    from ..metadata.tvdb import TVDBClient
    from ..scanner import scan_library

    tmdb = TMDBClient(settings.tmdb_api_key)
    tvdb = TVDBClient(settings.tvdb_api_key) if settings.tvdb_api_key.strip() else None
    lib_path = settings.movies_library_path if library_type == "movies" else settings.tv_library_path

    def _run() -> None:
        state.scan_status[library_type].running = True
        state.scan_status[library_type].last_error = None
        try:
            reports = scan_library(lib_path, library_type, settings, tmdb, tvdb)  # type: ignore[arg-type]
            state.scan_status[library_type].last_run = datetime.utcnow()
            for r in reports:
                state.add_activity(ActivityEntry.from_report(r, library_type))
                if r.result.value == "error_no_metadata":
                    _add_pending(state, r.source, library_type, r.message, settings)
        except Exception as exc:
            state.scan_status[library_type].last_error = str(exc)
        finally:
            state.scan_status[library_type].running = False
            tmdb.close()
            if tvdb:
                tvdb.close()

    started = state.start_scan(library_type, _run)
    if not started:
        raise HTTPException(status_code=409, detail=f"Scan for '{library_type}' is already running")

    return {"status": "started", "library_type": library_type}


def _add_pending(
    state: AppState,
    source: "Path",
    library_type: str,
    last_error: str,
    settings: Any,
) -> None:
    path_str = str(source)
    parsed = parse_filename(source, settings)
    state.add_pending(PendingItem(
        id=pending_id(path_str),
        path=path_str,
        library_type=library_type,
        parsed_title=parsed.raw_title,
        parsed_year=parsed.year,
        first_seen=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        last_error=last_error,
    ))


# ------------------------------------------------------------------
# Manual cleanup
# ------------------------------------------------------------------

@router.post("/cleanup")
def trigger_cleanup(state: StateDep) -> dict:
    from ..cleaner import clean_orphaned_dirs
    settings = get_settings()
    movies_cleaned = clean_orphaned_dirs(
        settings.movies_library_path,
        settings.min_file_size_bytes,
        settings.video_extensions,
        dry_run=settings.dry_run,
        delay_seconds=settings.io_delay_seconds,
    )
    tv_cleaned = clean_orphaned_dirs(
        settings.tv_library_path,
        settings.min_file_size_bytes,
        settings.video_extensions,
        dry_run=settings.dry_run,
        delay_seconds=settings.io_delay_seconds,
    )
    return {
        "movies_cleaned": movies_cleaned,
        "tv_cleaned": tv_cleaned,
        "dry_run": settings.dry_run,
    }


# ------------------------------------------------------------------
# Pending review queue
# ------------------------------------------------------------------

@router.get("/pending")
def get_pending(state: StateDep) -> dict:
    items = state.get_pending_list()
    return {"total": len(items), "items": [asdict(i) for i in items]}


class RetryBody(BaseModel):
    title: str
    year: int | None = None
    library_type: str | None = None


@router.post("/pending/{item_id}/retry")
def retry_pending(item_id: str, body: RetryBody, state: StateDep) -> dict:
    with state._lock:
        item = state.pending.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Pending item not found")

    from ..metadata.tmdb import TMDBClient
    from ..metadata.tvdb import TVDBClient
    from ..processor import process, ProcessResult

    settings = get_settings()
    tmdb = TMDBClient(settings.tmdb_api_key)
    tvdb = TVDBClient(settings.tvdb_api_key) if settings.tvdb_api_key.strip() else None
    lib_type = body.library_type or item.library_type

    try:
        report = process(
            Path(item.path),
            settings,
            tmdb,
            tvdb,
            lib_type,
            title_override=body.title.strip(),
            year_override=body.year,
        )
    finally:
        tmdb.close()
        if tvdb:
            tvdb.close()

    if report.result == ProcessResult.MOVED:
        state.remove_pending(item_id)
        state.add_activity(ActivityEntry.from_report(report, lib_type))
        return {"status": "moved", "destination": str(report.destination)}

    if report.result == ProcessResult.ERROR_NO_METADATA:
        return {"status": "error", "detail": f"Still no match for {body.title!r}"}

    # Any other result (skipped, dry_run, conflict, etc.) — clear from pending
    state.remove_pending(item_id)
    state.add_activity(ActivityEntry.from_report(report, lib_type))
    return {"status": report.result.value}


@router.delete("/pending/{item_id}", status_code=204)
def dismiss_pending(item_id: str, state: StateDep) -> Response:
    if not state.remove_pending(item_id):
        raise HTTPException(status_code=404, detail="Pending item not found")
    return Response(status_code=204)


# ------------------------------------------------------------------
# Library browser
# ------------------------------------------------------------------

@router.get("/library/{library_type}/tree")
def library_tree(
    library_type: str,
    path: str = Query(default=""),
) -> dict:
    if library_type not in ("movies", "tv"):
        raise HTTPException(status_code=400, detail="library_type must be 'movies' or 'tv'")

    settings = get_settings()
    lib_root = (
        settings.movies_library_path
        if library_type == "movies"
        else settings.tv_library_path
    ).resolve()

    if path:
        target = Path(path).resolve()
        try:
            target.relative_to(lib_root)
        except ValueError:
            raise HTTPException(status_code=400, detail="Path escapes library root")
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        return _node(target, lib_root, library_type, depth=1)
    else:
        # Root: return top 2 levels
        return _node(lib_root, lib_root, library_type, depth=2)


def _node(
    path: Path,
    lib_root: Path,
    library_type: str,
    depth: int,
) -> dict[str, Any]:
    settings = get_settings()
    is_dir = path.is_dir()
    size: int | None = None

    if not is_dir:
        try:
            size = path.stat().st_size
        except OSError:
            size = None

    organized = False
    if not is_dir:
        if library_type == "movies":
            organized = movie_org.is_organized(path, lib_root)
        else:
            organized = tv_org.is_organized(path, lib_root)

    children: list | None = None
    if is_dir and depth > 0:
        children = []
        try:
            for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
                child_path = Path(entry.path)
                # Only include video files and directories (skip metadata, subtitles, etc.)
                if entry.is_file():
                    if child_path.suffix.lower() not in settings.video_extensions:
                        continue
                children.append(_node(child_path, lib_root, library_type, depth - 1))
        except OSError:
            pass

    return {
        "path": str(path),
        "name": path.name,
        "is_dir": is_dir,
        "size_bytes": size,
        "organized": organized,
        "children": children,
    }


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    dry_run: bool | None = None
    log_level: str | None = None
    min_file_size_mb: int | None = None
    settle_delay: int | None = None
    scan_workers: int | None = None
    io_delay_seconds: float | None = None
    ui_poll_interval_seconds: float | None = None


def _mask_key(key: str) -> str:
    if not key:
        return ""
    visible = key[-6:] if len(key) >= 6 else key
    return "•" * max(0, len(key) - 6) + visible


@router.get("/settings")
def get_settings_endpoint() -> dict:
    s = get_settings()
    return {
        "dry_run": s.dry_run,
        "log_level": s.log_level,
        "min_file_size_mb": s.min_file_size_mb,
        "settle_delay": s.settle_delay,
        "scan_workers": s.scan_workers,
        "io_delay_seconds": s.io_delay_seconds,
        "ui_poll_interval_seconds": s.ui_poll_interval_seconds,
        "stability_check_interval": s.stability_check_interval,
        "stability_check_retries": s.stability_check_retries,
        "tmdb_api_key": _mask_key(s.tmdb_api_key),
        "tvdb_api_key": _mask_key(s.tvdb_api_key),
        "tv_library_path": str(s.tv_library_path),
        "movies_library_path": str(s.movies_library_path),
    }


@router.post("/settings")
def post_settings(body: SettingsUpdate) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="No editable fields provided")
    update_settings(patch)
    return get_settings_endpoint()
