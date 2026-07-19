"""Concurrent film metadata resolution with caching and graceful fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.models import FilmDetails, FilmRef
from letterboxd_scraper.parsing import parse_film_html, parse_film_markdown


class FilmResolver:
    """Resolve film details concurrently while preserving deterministic output."""

    def __init__(self, http: HttpClient, cache: FilmCache, *, concurrency: int) -> None:
        self._http = http
        self._cache = cache
        self._concurrency = concurrency

    def resolve_many(
        self, films: dict[str, FilmRef]
    ) -> tuple[list[FilmDetails], list[FilmDetails]]:
        resolved: list[FilmDetails] = []
        unresolved: list[FilmDetails] = []

        with ThreadPoolExecutor(max_workers=self._concurrency) as executor:
            futures = {executor.submit(self.resolve_one, film): film for film in films.values()}
            for future in as_completed(futures):
                fallback = futures[future]
                try:
                    details = future.result()
                except Exception as exc:  # A failed title must not abort the complete dataset.
                    details = FilmDetails(
                        uri=fallback.uri,
                        title=fallback.title,
                        year=fallback.year,
                        average_rating=None,
                        error=repr(exc),
                    )
                (resolved if details.is_complete else unresolved).append(details)

        resolved.sort(key=_stable_film_sort_key)
        unresolved.sort(key=_stable_film_sort_key)
        return resolved, unresolved

    def resolve_one(self, film: FilmRef) -> FilmDetails:
        cached = self._cache.get(film.uri)
        if cached is not None:
            return cached

        try:
            response = self._http.get(film.uri)
            details = parse_film_html(response.text, film)
        except Exception:
            response = self._http.get_jina(film.uri)
            details = parse_film_markdown(response.text, film)

        self._cache.put(details)
        return details


def _stable_film_sort_key(film: FilmDetails) -> tuple[str, int, str]:
    return (film.title.casefold(), film.year or 0, film.uri)
