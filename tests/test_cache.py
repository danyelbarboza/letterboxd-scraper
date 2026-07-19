import os
import time
from pathlib import Path

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig
from letterboxd_scraper.models import FilmDetails


def make_details() -> FilmDetails:
    return FilmDetails(
        uri="https://letterboxd.com/film/example/",
        title="Example",
        year=2020,
        average_rating=3.5,
        rating_source="twitter:data2",
        metadata_source="film-html",
    )


def test_cache_round_trip(tmp_path: Path) -> None:
    cache = FilmCache(CacheConfig(enabled=True, directory=tmp_path, ttl_hours=24))
    cache.put(make_details())

    cached = cache.get(make_details().uri)

    assert cached is not None
    assert cached.title == "Example"
    assert cached.average_rating == 3.5


def test_cache_ignores_expired_entries(tmp_path: Path) -> None:
    cache = FilmCache(CacheConfig(enabled=True, directory=tmp_path, ttl_hours=0.001))
    cache.put(make_details())
    path = next(tmp_path.glob("*.json"))
    old = time.time() - 60
    os.utime(path, (old, old))

    assert cache.get(make_details().uri) is None


def test_disabled_cache_is_a_noop(tmp_path: Path) -> None:
    cache = FilmCache(CacheConfig(enabled=False, directory=tmp_path, ttl_hours=24))
    cache.put(make_details())
    assert cache.get(make_details().uri) is None
    assert list(tmp_path.iterdir()) == []
