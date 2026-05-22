from __future__ import annotations

import re
from pathlib import Path

from ..metadata.tmdb import TMDBEpisode
from ..parser import sanitize_filename

# Matches "Show Name - S01E01" or "Show Name - S01E01E02"
_ORGANIZED_PATTERN = re.compile(r"^.+ - S\d{2}E\d{2}", re.IGNORECASE)
_SEASON_FOLDER_PATTERN = re.compile(r"^Season \d{2}$", re.IGNORECASE)


def compute_destination(
    episode: TMDBEpisode,
    source_ext: str,
    library_path: Path,
    episode_end: int | None = None,
) -> Path:
    """
    /mnt/storage/TV Shows/Show Name/Season 01/Show Name - S01E01 - Episode Title.ext
    """
    show_folder = sanitize_filename(episode.show_name)
    season_folder = f"Season {episode.season:02d}"

    ep_tag = f"S{episode.season:02d}E{episode.episode:02d}"
    if episode_end is not None and episode_end > episode.episode:
        ep_tag += f"E{episode_end:02d}"

    file_stem = f"{show_folder} - {ep_tag}"
    if episode.episode_title:
        file_stem += f" - {sanitize_filename(episode.episode_title)}"

    return library_path / show_folder / season_folder / f"{file_stem}{source_ext.lower()}"


def is_organized(path: Path, library_path: Path) -> bool:
    """
    True if path is already inside Season XX/ and its filename matches
    the "Show Name - SXXEXX" pattern.
    """
    try:
        relative = path.relative_to(library_path)
    except ValueError:
        return False

    parts = relative.parts
    # Expect: ShowName / Season XX / episode_file
    if len(parts) != 3:
        return False

    season_part = parts[1]
    filename = parts[2]
    stem = Path(filename).stem

    return bool(_SEASON_FOLDER_PATTERN.match(season_part)) and bool(
        _ORGANIZED_PATTERN.match(stem)
    )
