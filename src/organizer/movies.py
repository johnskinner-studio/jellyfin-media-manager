from __future__ import annotations

from pathlib import Path

from ..metadata.tmdb import TMDBMovie
from ..parser import sanitize_filename


def compute_destination(movie: TMDBMovie, source_ext: str, library_path: Path) -> Path:
    """
    /mnt/storage/Movies/Movie Title (Year)/Movie Title (Year).ext
    """
    folder_name = sanitize_filename(f"{movie.title} ({movie.year})")
    file_name = f"{folder_name}{source_ext.lower()}"
    return library_path / folder_name / file_name


def is_organized(path: Path, library_path: Path) -> bool:
    """
    True if path looks like an already-organized movie file:
    library/Movie Title (Year)/Movie Title (Year).ext
    """
    import re

    try:
        relative = path.relative_to(library_path)
    except ValueError:
        return False

    parts = relative.parts
    if len(parts) != 2:
        return False

    folder, filename = parts
    stem = Path(filename).stem
    # Both folder and filename stem must match "Title (Year)" pattern
    year_pattern = re.compile(r".+\(\d{4}\)$")
    return bool(year_pattern.match(folder)) and stem == folder
