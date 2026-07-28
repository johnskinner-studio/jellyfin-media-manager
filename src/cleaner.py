from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def remove_empty_dirs(root: Path, stop_at: Path, dry_run: bool = False) -> None:
    """
    Walk root bottom-up and remove empty directories.
    Never removes stop_at itself or its direct children (library-root folders).
    """
    if not root.exists() or not root.is_dir():
        return

    # Collect all subdirs depth-first, deepest first
    subdirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )

    for dirpath in subdirs:
        # Never touch the library root or its immediate children
        if dirpath == stop_at:
            continue
        try:
            if dirpath.relative_to(stop_at) and len(dirpath.relative_to(stop_at).parts) == 1:
                continue
        except ValueError:
            continue

        if dirpath.is_dir() and not any(dirpath.iterdir()):
            if dry_run:
                log.info("[DRY RUN] Would remove empty dir: %s", dirpath)
            else:
                try:
                    dirpath.rmdir()
                    log.debug("Removed empty dir: %s", dirpath)
                except OSError as exc:
                    log.warning("Could not remove dir %s: %s", dirpath, exc)


def clean_orphaned_dirs(
    library_path: Path,
    min_file_size_bytes: int,
    video_extensions: frozenset[str],
    dry_run: bool = False,
) -> int:
    """
    Walk the top level of library_path and remove any directory that contains
    no eligible video files — these are leftover download folders whose video
    was already moved to the organised location.  Returns the number of
    directories cleaned.
    """
    if not library_path.exists():
        return 0

    cleaned = 0
    for item in sorted(library_path.iterdir()):
        if not item.is_dir():
            continue
        has_video = any(
            f.is_file()
            and f.suffix.lower() in video_extensions
            and f.stat().st_size >= min_file_size_bytes
            for f in item.rglob("*")
        )
        if has_video:
            continue
        log.info("%sRemoving orphaned download dir: %s", "[DRY RUN] Would remove" if dry_run else "", item)
        cleanup_source(item, library_path, dry_run=dry_run)
        cleaned += 1
    return cleaned


def cleanup_source(
    source_dir: Path,
    stop_at: Path,
    dry_run: bool = False,
) -> None:
    """
    After a video file has been moved out of source_dir, delete everything
    that remains (all files regardless of extension, then empty directories)
    and remove source_dir itself.
    """
    if source_dir == stop_at or not source_dir.exists():
        return

    # Delete all remaining files (rar, nfo, jpg, srt, sfv, sample, etc.)
    for f in source_dir.rglob("*"):
        if not f.is_file():
            continue
        if dry_run:
            log.info("[DRY RUN] Would delete: %s", f)
        else:
            try:
                f.unlink()
                log.debug("Deleted: %s", f)
            except OSError as exc:
                log.warning("Could not delete %s: %s", f, exc)

    # Remove subdirectories bottom-up, then the source dir itself
    for d in sorted(source_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not d.is_dir():
            continue
        if dry_run:
            log.info("[DRY RUN] Would remove dir: %s", d)
        else:
            try:
                d.rmdir()
                log.debug("Removed dir: %s", d)
            except OSError as exc:
                log.warning("Could not remove dir %s: %s", d, exc)

    if dry_run:
        log.info("[DRY RUN] Would remove source dir: %s", source_dir)
    else:
        try:
            source_dir.rmdir()
            log.debug("Removed source dir: %s", source_dir)
        except OSError as exc:
            log.warning("Could not remove source dir %s: %s", source_dir, exc)
