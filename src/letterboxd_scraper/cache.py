"""Small file cache for resolved film pages and parsed metadata."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from letterboxd_scraper.config import CacheConfig
from letterboxd_scraper.models import FilmDetails


class FilmCache:
    """Persist film metadata as one JSON document per canonical URI."""

    def __init__(self, config: CacheConfig) -> None:
        self._config = config
        if config.enabled:
            config.directory.mkdir(parents=True, exist_ok=True)

    def get(self, uri: str) -> FilmDetails | None:
        if not self._config.enabled:
            return None
        path = self._path(uri)
        if not path.exists() or self._is_expired(path):
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return FilmDetails(
                uri=str(payload["uri"]),
                title=str(payload.get("title", "")),
                year=_optional_int(payload.get("year")),
                average_rating=_optional_float(payload.get("average_rating")),
                rating_source=str(payload.get("rating_source", "")),
                metadata_source=str(payload.get("metadata_source", "cache")),
                error=str(payload.get("error", "")),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def put(self, details: FilmDetails) -> None:
        if not self._config.enabled:
            return
        payload: dict[str, Any] = {
            "uri": details.uri,
            "title": details.title,
            "year": details.year,
            "average_rating": details.average_rating,
            "rating_source": details.rating_source,
            "metadata_source": details.metadata_source,
            "error": details.error,
            "cached_at_epoch": time.time(),
        }
        path = self._path(details.uri)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _path(self, uri: str) -> Path:
        key = hashlib.sha256(uri.encode("utf-8")).hexdigest()
        return self._config.directory / f"{key}.json"

    def _is_expired(self, path: Path) -> bool:
        ttl_seconds = self._config.ttl_hours * 3600
        return ttl_seconds >= 0 and (time.time() - path.stat().st_mtime) > ttl_seconds


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"Unsupported integer cache value: {type(value).__name__}")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"Unsupported float cache value: {type(value).__name__}")
