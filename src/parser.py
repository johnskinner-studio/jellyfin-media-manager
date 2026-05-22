from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import guessit

if TYPE_CHECKING:
    from .config import Settings

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_DOTS_SPACES = re.compile(r"[\s.]+$")
_LEADING_DOTS_SPACES = re.compile(r"^[\s.]+")


def sanitize_filename(name: str) -> str:
    """Remove filesystem-illegal characters and normalise whitespace."""
    name = _ILLEGAL_CHARS.sub("", name)
    name = _LEADING_DOTS_SPACES.sub("", name)
    name = _TRAILING_DOTS_SPACES.sub("", name)
    # Collapse runs of whitespace
    name = re.sub(r"\s{2,}", " ", name)
    return name.strip()


@dataclass
class ParsedMedia:
    raw_title: str
    media_type: Literal["movie", "episode", "unknown"]
    source_path: Path
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None  # for multi-episode files S01E01E02
    confidence: float = 0.0


def parse_filename(path: Path, settings: "Settings | None" = None) -> ParsedMedia:
    """Parse a media filename using guessit and return a normalised ParsedMedia."""
    # Use only the filename stem — parent dir names can mislead guessit
    result = guessit.guessit(path.name)

    raw_type = result.get("type", "unknown")
    media_type: Literal["movie", "episode", "unknown"]

    if raw_type == "movie":
        media_type = "movie"
    elif raw_type == "episode":
        media_type = "episode"
    else:
        # Fallback: infer from parent path keywords
        parent_str = str(path).lower()
        if "tv show" in parent_str or "/tv/" in parent_str or "season" in parent_str:
            media_type = "episode"
        elif "movie" in parent_str:
            media_type = "movie"
        else:
            media_type = "unknown"

    title = result.get("title", "")
    year: int | None = result.get("year")

    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None

    if media_type == "episode":
        raw_season = result.get("season")
        season = int(raw_season) if raw_season is not None else 1  # anime default

        raw_ep = result.get("episode")
        if isinstance(raw_ep, list):
            episode = int(raw_ep[0])
            episode_end = int(raw_ep[-1]) if len(raw_ep) > 1 else None
        elif raw_ep is not None:
            episode = int(raw_ep)

    confidence = _compute_confidence(title, media_type, year, season, episode)

    return ParsedMedia(
        raw_title=title,
        media_type=media_type,
        source_path=path,
        year=year,
        season=season,
        episode=episode,
        episode_end=episode_end,
        confidence=confidence,
    )


def _compute_confidence(
    title: str,
    media_type: Literal["movie", "episode", "unknown"],
    year: int | None,
    season: int | None,
    episode: int | None,
) -> float:
    score = 0.0
    if title and len(title) >= 3:
        score += 0.3
    if media_type != "unknown":
        score += 0.2
    if year is not None:
        score += 0.2
    if season is not None:
        score += 0.15
    if episode is not None:
        score += 0.15
    return min(score, 1.0)


def is_video_file(path: Path, settings: "Settings") -> bool:
    return path.suffix.lower() in settings.video_extensions


def is_ignored_file(path: Path, settings: "Settings") -> bool:
    if path.suffix.lower() in settings.ignored_extensions:
        return True
    name_lower = path.name.lower()
    return any(pat in name_lower for pat in settings.ignored_name_patterns)
