from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_JSON_PATH = Path("config.json")

_EDITABLE_FIELDS = frozenset(
    {"dry_run", "log_level", "min_file_size_mb", "settle_delay", "scan_workers"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    tmdb_api_key: str
    tvdb_api_key: str = ""

    tv_library_path: Path = Path("/mnt/storage/TV Shows")
    movies_library_path: Path = Path("/mnt/storage/Movies")

    dry_run: bool = False
    log_level: str = "INFO"
    scan_on_start: bool = True
    min_file_size_mb: int = 100

    # Seconds between size polls when checking file stability
    stability_check_interval: int = 5
    # How many consecutive equal-size reads = stable
    stability_check_retries: int = 6

    # How many seconds after the last watchdog event to wait before processing
    settle_delay: int = 10

    # Worker threads for the startup scan
    scan_workers: int = 4

    video_extensions: frozenset[str] = frozenset(
        {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv"}
    )
    ignored_extensions: frozenset[str] = frozenset(
        {
            ".nfo",
            ".jpg",
            ".jpeg",
            ".png",
            ".srt",
            ".ass",
            ".ssa",
            ".sub",
            ".idx",
            ".sfv",
            ".md5",
            ".txt",
        }
    )
    # Filenames containing these substrings (case-insensitive) are skipped
    ignored_name_patterns: tuple[str, ...] = ("sample", "trailer", "featurette")

    @field_validator("tmdb_api_key")
    @classmethod
    def tmdb_key_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("TMDB_API_KEY must be set")
        return v

    @property
    def min_file_size_bytes(self) -> int:
        return self.min_file_size_mb * 1024 * 1024


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = _apply_config_json(Settings())
    return _settings


def _apply_config_json(base: Settings) -> Settings:
    if not CONFIG_JSON_PATH.exists():
        return base
    try:
        data = json.loads(CONFIG_JSON_PATH.read_text())
        # Only apply known editable fields to avoid surprises
        overlay = {k: v for k, v in data.items() if k in _EDITABLE_FIELDS}
        if overlay:
            return base.model_copy(update=overlay)
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to load config.json: %s", exc)
    return base


def update_settings(patch: dict) -> Settings:
    """Update the in-memory Settings singleton and persist editable fields to config.json."""
    global _settings
    safe_patch = {k: v for k, v in patch.items() if k in _EDITABLE_FIELDS}
    current = get_settings()
    _settings = current.model_copy(update=safe_patch)
    try:
        # Merge with any existing config.json so we preserve keys not in this patch
        existing: dict = {}
        if CONFIG_JSON_PATH.exists():
            existing = json.loads(CONFIG_JSON_PATH.read_text())
        existing.update(safe_patch)
        CONFIG_JSON_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to write config.json: %s", exc)
    return _settings
