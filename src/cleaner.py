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
