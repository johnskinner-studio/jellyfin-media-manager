from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .cleaner import remove_empty_dirs
from .metadata.tmdb import TMDBClient, TMDBEpisode, TMDBMovie
from .organizer import movies as movie_organizer
from .organizer import tv as tv_organizer
from .parser import ParsedMedia, is_ignored_file, is_video_file, parse_filename

if TYPE_CHECKING:
    from .config import Settings
    from .metadata.tvdb import TVDBClient

log = logging.getLogger(__name__)


class ProcessResult(Enum):
    MOVED = "moved"
    DRY_RUN = "dry_run"
    SKIPPED_ALREADY_ORGANIZED = "skipped_already_organized"
    SKIPPED_TOO_SMALL = "skipped_too_small"
    SKIPPED_NOT_VIDEO = "skipped_not_video"
    SKIPPED_UNSTABLE = "skipped_unstable"
    ERROR_NO_METADATA = "error_no_metadata"
    ERROR_CONFLICT = "error_conflict"
    ERROR_EXCEPTION = "error_exception"


@dataclass
class ProcessReport:
    source: Path
    result: ProcessResult
    destination: Path | None = None
    message: str = ""


def process(
    candidate: Path,
    settings: "Settings",
    tmdb: TMDBClient,
    tvdb: "TVDBClient | None",
    library_type: str,  # "movies" or "tv"
) -> ProcessReport:
    try:
        return _process(candidate, settings, tmdb, tvdb, library_type)
    except Exception as exc:
        log.exception("Unexpected error processing %s", candidate)
        return ProcessReport(
            source=candidate,
            result=ProcessResult.ERROR_EXCEPTION,
            message=str(exc),
        )


def _process(
    candidate: Path,
    settings: "Settings",
    tmdb: TMDBClient,
    tvdb: "TVDBClient | None",
    library_type: str,
) -> ProcessReport:
    # 1. Resolve actual video file
    if candidate.is_dir():
        video_file = _extract_video_from_folder(candidate, settings)
    else:
        video_file = candidate if is_video_file(candidate, settings) else None

    if video_file is None:
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_NOT_VIDEO,
            message="No eligible video file found",
        )

    # 2. Size check
    if video_file.stat().st_size < settings.min_file_size_bytes:
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_TOO_SMALL,
            message=f"File size {video_file.stat().st_size} < {settings.min_file_size_bytes}",
        )

    # 3. File stability check
    if not _is_stable(video_file, settings):
        log.info("File still being written, skipping for now: %s", video_file)
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_UNSTABLE,
            message="File size is still changing",
        )

    # 4. Deduplication — quick check before any API calls
    library_path = (
        settings.movies_library_path
        if library_type == "movies"
        else settings.tv_library_path
    )
    if library_type == "movies" and movie_organizer.is_organized(video_file, library_path):
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_ALREADY_ORGANIZED,
        )
    if library_type == "tv" and tv_organizer.is_organized(video_file, library_path):
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_ALREADY_ORGANIZED,
        )

    # 5. Parse filename
    parsed = parse_filename(video_file, settings)

    # 6. Metadata lookup
    destination = _resolve_destination(
        parsed, video_file, library_path, library_type, settings, tmdb, tvdb
    )
    if destination is None:
        log.warning("Could not resolve metadata for %s", video_file)
        return ProcessReport(
            source=candidate,
            result=ProcessResult.ERROR_NO_METADATA,
            message=f"No TMDB match found for: {parsed.raw_title!r}",
        )

    # 7. Already at destination?
    if video_file.resolve() == destination.resolve():
        return ProcessReport(
            source=candidate,
            result=ProcessResult.SKIPPED_ALREADY_ORGANIZED,
            destination=destination,
        )

    # 8. Conflict check
    destination = _resolve_conflict(video_file, destination, settings)
    if destination is None:
        return ProcessReport(
            source=candidate,
            result=ProcessResult.ERROR_CONFLICT,
            message="Destination exists and appears to be a duplicate; source deleted",
        )

    # 9. Dry-run gate
    if settings.dry_run:
        log.info("[DRY RUN] Would move:\n  %s\n  → %s", video_file, destination)
        return ProcessReport(
            source=candidate,
            result=ProcessResult.DRY_RUN,
            destination=destination,
        )

    # 10. Perform move
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(video_file), str(destination))
    log.info("Moved: %s → %s", video_file, destination)

    # 11. Clean up source folder if candidate was a directory
    if candidate.is_dir() and candidate.exists():
        remove_empty_dirs(candidate, library_path, dry_run=False)

    return ProcessReport(
        source=candidate,
        result=ProcessResult.MOVED,
        destination=destination,
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _extract_video_from_folder(folder: Path, settings: "Settings") -> Path | None:
    """Return the largest eligible video file inside folder (recursively)."""
    candidates = [
        f
        for f in folder.rglob("*")
        if f.is_file()
        and is_video_file(f, settings)
        and not is_ignored_file(f, settings)
        and f.stat().st_size >= settings.min_file_size_bytes
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_size)


def _is_stable(path: Path, settings: "Settings") -> bool:
    """Return True if the file size hasn't changed over the polling window."""
    prev_size = path.stat().st_size
    for _ in range(settings.stability_check_retries):
        time.sleep(settings.stability_check_interval)
        if not path.exists():
            return False
        curr_size = path.stat().st_size
        if curr_size != prev_size:
            return False
        prev_size = curr_size
    return True


def _resolve_destination(
    parsed: ParsedMedia,
    video_file: Path,
    library_path: Path,
    library_type: str,
    settings: "Settings",
    tmdb: TMDBClient,
    tvdb: "TVDBClient | None",
) -> Path | None:
    ext = video_file.suffix

    if library_type == "movies":
        movie = tmdb.search_movie(parsed.raw_title, parsed.year)
        if movie is None:
            return None
        return movie_organizer.compute_destination(movie, ext, library_path)

    # TV
    if parsed.season is None or parsed.episode is None:
        log.warning("Cannot determine season/episode for %s", video_file.name)
        return None

    show = tmdb.search_tv(parsed.raw_title, parsed.year)
    if show is None:
        return None

    episode = tmdb.get_episode(
        show.tmdb_id, show.name, show.year, parsed.season, parsed.episode
    )

    # TVDB fallback for episode title
    if episode is not None and not episode.episode_title and tvdb is not None:
        series = tvdb.search_series(show.name)
        if series:
            title = tvdb.get_episode_name(series["id"], parsed.season, parsed.episode)
            if title:
                episode.episode_title = title

    if episode is None:
        # Construct a minimal episode record from parsed data so we can still organize
        from .metadata.tmdb import TMDBEpisode as _TMDBEpisode
        episode = _TMDBEpisode(
            show_id=show.tmdb_id,
            show_name=show.name,
            season=parsed.season,
            episode=parsed.episode,
            episode_title="",
            show_year=show.year,
        )

    return tv_organizer.compute_destination(episode, ext, library_path, parsed.episode_end)


def _resolve_conflict(
    source: Path,
    destination: Path,
    settings: "Settings",
) -> Path | None:
    """
    Handle the case where destination already exists.
    - Same file size → treat as duplicate, delete source, return None
    - Different size → find a free path by appending .(N)
    """
    if not destination.exists():
        return destination

    src_size = source.stat().st_size
    dst_size = destination.stat().st_size

    if src_size == dst_size:
        log.warning(
            "Duplicate detected (same size), deleting source: %s", source
        )
        if not settings.dry_run:
            source.unlink(missing_ok=True)
        return None

    # Different content — find a free name
    for n in range(1, 6):
        stem = destination.stem
        candidate = destination.with_name(f"{stem}.({n}){destination.suffix}")
        if not candidate.exists():
            log.warning(
                "Conflict at %s, using %s instead", destination, candidate.name
            )
            return candidate

    log.error("Could not find a free destination after 5 attempts for %s", destination)
    return None
