from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .organizer import movies as movie_organizer
from .organizer import tv as tv_organizer
from .parser import is_video_file
from .processor import ProcessReport, process

if TYPE_CHECKING:
    from .config import Settings
    from .metadata.tmdb import TMDBClient
    from .metadata.tvdb import TVDBClient

log = logging.getLogger(__name__)


def scan_library(
    library_path: Path,
    media_type: Literal["movies", "tv"],
    settings: "Settings",
    tmdb: "TMDBClient",
    tvdb: "TVDBClient | None",
) -> list[ProcessReport]:
    if not library_path.exists():
        log.warning("Library path does not exist, skipping scan: %s", library_path)
        return []

    candidates = _collect_candidates(library_path, media_type, settings)
    log.info(
        "Scan found %d candidate(s) in %s", len(candidates), library_path
    )

    if not candidates:
        return []

    reports: list[ProcessReport] = []
    with ThreadPoolExecutor(max_workers=settings.scan_workers) as pool:
        futures = {
            pool.submit(process, c, settings, tmdb, tvdb, media_type): c
            for c in candidates
        }
        for fut in as_completed(futures):
            report = fut.result()
            _log_report(report)
            reports.append(report)

    return reports


def _collect_candidates(
    library_path: Path,
    media_type: Literal["movies", "tv"],
    settings: "Settings",
) -> list[Path]:
    """
    Collect items that need processing from the top level of library_path.

    Strategy:
    - Direct video files in the library root → candidate
    - Sub-folders in the library root that are NOT already fully organized → candidate
    """
    candidates: list[Path] = []

    for item in library_path.iterdir():
        if item.is_file():
            if (
                is_video_file(item, settings)
                and item.stat().st_size >= settings.min_file_size_bytes
            ):
                candidates.append(item)
        elif item.is_dir():
            if media_type == "movies":
                # Check if every video file inside is already organized
                videos = _find_videos(item, settings)
                if not videos:
                    continue
                if all(movie_organizer.is_organized(v, library_path) for v in videos):
                    log.debug("Already organized, skipping: %s", item)
                    continue
            else:  # tv
                # Walk one level deeper (season folders)
                videos = _find_videos(item, settings)
                if not videos:
                    continue
                if all(tv_organizer.is_organized(v, library_path) for v in videos):
                    log.debug("Already organized, skipping: %s", item)
                    continue

            candidates.append(item)

    return candidates


def _find_videos(folder: Path, settings: "Settings") -> list[Path]:
    return [
        f
        for f in folder.rglob("*")
        if f.is_file()
        and is_video_file(f, settings)
        and f.stat().st_size >= settings.min_file_size_bytes
    ]


def _log_report(report: ProcessReport) -> None:
    from .processor import ProcessResult

    if report.result == ProcessResult.MOVED:
        log.info("Organized: %s → %s", report.source, report.destination)
    elif report.result == ProcessResult.SKIPPED_ALREADY_ORGANIZED:
        log.debug("Already organized: %s", report.source)
    elif report.result in (
        ProcessResult.ERROR_NO_METADATA,
        ProcessResult.ERROR_CONFLICT,
        ProcessResult.ERROR_EXCEPTION,
    ):
        log.warning("Failed [%s]: %s — %s", report.result.value, report.source, report.message)
    else:
        log.debug("[%s] %s", report.result.value, report.source)
