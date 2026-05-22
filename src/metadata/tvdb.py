from __future__ import annotations

import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_TOKEN_URL = "https://api4.thetvdb.com/v4/login"
_BASE_URL = "https://api4.thetvdb.com/v4"


class TVDBClient:
    """TVDB v4 client used as a secondary source for TV episode titles."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._token: str | None = None
        self._client = httpx.Client(base_url=_BASE_URL, timeout=10.0)

    def _ensure_auth(self) -> None:
        if self._token:
            return
        try:
            resp = httpx.post(_TOKEN_URL, json={"apikey": self._api_key}, timeout=10.0)
            resp.raise_for_status()
            self._token = resp.json()["data"]["token"]
            self._client.headers["Authorization"] = f"Bearer {self._token}"
            log.debug("TVDB authentication successful")
        except Exception as exc:
            log.warning("TVDB authentication failed: %s", exc)
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def search_series(self, title: str) -> dict | None:
        self._ensure_auth()
        try:
            resp = self._client.get("/search", params={"query": title, "type": "series"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("TVDB search failed: %s", exc)
            raise

        results = resp.json().get("data", [])
        if not results:
            return None
        return results[0]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    def get_episode_name(self, series_id: int, season: int, episode: int) -> str | None:
        """Return the episode title string, or None if not found."""
        self._ensure_auth()
        try:
            resp = self._client.get(
                f"/series/{series_id}/episodes/default",
                params={"season": season, "episodeNumber": episode, "page": 0},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            log.warning("TVDB episode fetch failed: %s", exc)
            raise
        except httpx.HTTPError as exc:
            log.warning("TVDB episode fetch failed: %s", exc)
            raise

        episodes = resp.json().get("data", {}).get("episodes", [])
        for ep in episodes:
            if ep.get("seasonNumber") == season and ep.get("number") == episode:
                return ep.get("name") or None
        return None

    def close(self) -> None:
        self._client.close()
