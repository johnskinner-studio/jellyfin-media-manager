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


def cleanup_source(
    source_dir: Path,
    stop_at: Path,
    ignored_extensions: frozenset[str],
    dry_run: bool = False,
) -> None:
    """
    After a video file has been moved out of source_dir, delete leftover
    junk files (nfo, jpg, srt, etc.) and then remove empty directories
    including source_dir itself if it ends up empty.
    """
    if source_dir == stop_at or not source_dir.exists():
        return

    # Delete ignorable leftover files (nfo, jpg, srt, sfv, …)
    for f in list(source_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in ignored_extensions:
            if dry_run:
                log.info("[DRY RUN] Would delete leftover: %s", f)
            else:
                try:
                    f.unlink()
                    log.debug("Deleted leftover: %s", f)
                except OSError as exc:
                    log.warning("Could not delete %s: %s", f, exc)

    # Remove empty subdirectories bottom-up
    remove_empty_dirs(source_dir, stop_at, dry_run=dry_run)

    # Remove source_dir itself if now empty (remove_empty_dirs skips it
    # when it is a direct child of stop_at, so handle that here)
    if source_dir.is_dir() and not any(source_dir.iterdir()):
        if dry_run:
            log.info("[DRY RUN] Would remove source dir: %s", source_dir)
        else:
            try:
                source_dir.rmdir()
                log.debug("Removed source dir: %s", source_dir)
            except OSError as exc:
                log.warning("Could not remove source dir %s: %s", source_dir, exc)
