from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"
_MATCH_THRESHOLD = 0.6


@dataclass
class TMDBMovie:
    tmdb_id: int
    title: str
    year: int
    original_title: str


@dataclass
class TMDBShow:
    tmdb_id: int
    name: str
    year: int


@dataclass
class TMDBEpisode:
    show_id: int
    show_name: str
    season: int
    episode: int
    episode_title: str
    show_year: int


class TMDBClient:
    def __init__(self, api_key: str) -> None:
        self._client = httpx.Client(
            base_url=_BASE_URL,
            params={"api_key": api_key},
            timeout=10.0,
        )

    # ------------------------------------------------------------------
    # Movies
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def search_movie(self, title: str, year: int | None) -> TMDBMovie | None:
        params: dict = {"query": title, "include_adult": "false"}
        if year:
            params["year"] = year
        try:
            resp = self._client.get("/search/movie", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("TMDB movie search failed: %s", exc)
            raise

        results = resp.json().get("results", [])
        if not results:
            return None

        best = _best_title_match(title, year, results, title_key="title", date_key="release_date")
        if best is None:
            return None

        release_year = _parse_year(best.get("release_date", ""))
        return TMDBMovie(
            tmdb_id=best["id"],
            title=best["title"],
            year=release_year or year or 0,
            original_title=best.get("original_title", best["title"]),
        )

    # ------------------------------------------------------------------
    # TV shows
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def search_tv(self, title: str, year: int | None) -> TMDBShow | None:
        params: dict = {"query": title}
        if year:
            params["first_air_date_year"] = year
        try:
            resp = self._client.get("/search/tv", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("TMDB TV search failed: %s", exc)
            raise

        results = resp.json().get("results", [])
        if not results:
            return None

        best = _best_title_match(title, year, results, title_key="name", date_key="first_air_date")
        if best is None:
            return None

        show_year = _parse_year(best.get("first_air_date", ""))
        return TMDBShow(
            tmdb_id=best["id"],
            name=best["name"],
            year=show_year or year or 0,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def get_episode(self, show_id: int, show_name: str, show_year: int, season: int, episode: int) -> TMDBEpisode | None:
        try:
            resp = self._client.get(f"/tv/{show_id}/season/{season}/episode/{episode}")
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                log.debug("TMDB episode not found: show=%s S%02dE%02d", show_id, season, episode)
                return None
            log.warning("TMDB episode fetch failed: %s", exc)
            raise
        except httpx.HTTPError as exc:
            log.warning("TMDB episode fetch failed: %s", exc)
            raise

        data = resp.json()
        return TMDBEpisode(
            show_id=show_id,
            show_name=show_name,
            season=season,
            episode=episode,
            episode_title=data.get("name", ""),
            show_year=show_year,
        )

    def close(self) -> None:
        self._client.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _best_title_match(
    query: str,
    year: int | None,
    results: list[dict],
    title_key: str,
    date_key: str,
) -> dict | None:
    query_lower = query.lower()

    def score(item: dict) -> float:
        title = item.get(title_key, "")
        title_score = difflib.SequenceMatcher(None, query_lower, title.lower()).ratio()
        year_bonus = 0.0
        if year:
            item_year = _parse_year(item.get(date_key, ""))
            if item_year and abs(item_year - year) <= 1:
                year_bonus = 0.15
        return title_score + year_bonus

    scored = sorted(results, key=score, reverse=True)
    best = scored[0]
    if score(best) < _MATCH_THRESHOLD:
        log.debug("No confident TMDB match for %r (best score %.2f)", query, score(best))
        return None
    return best


def _parse_year(date_str: str) -> int | None:
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            pass
    return None
