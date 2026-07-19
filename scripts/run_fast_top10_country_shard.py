from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import generate_top10_by_country_shard as shard
from letterboxd_scraper.models import FilmDetails, FilmRef
from letterboxd_scraper.parsing import parse_film_html, parse_film_markdown


class JinaFirstFilmResolver:
    """Resolve film metadata through the hosted-runner-friendly path first."""

    def __init__(self, http, cache, *, concurrency: int) -> None:
        self._http = http
        self._cache = cache
        self._concurrency = max(24, concurrency)

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
                except Exception as exc:
                    details = FilmDetails(
                        uri=fallback.uri,
                        title=fallback.title,
                        year=fallback.year,
                        average_rating=None,
                        error=repr(exc),
                    )
                (resolved if details.is_complete else unresolved).append(details)

        sort_key = lambda film: (film.title.casefold(), film.year or 0, film.uri)
        resolved.sort(key=sort_key)
        unresolved.sort(key=sort_key)
        return resolved, unresolved

    def resolve_one(self, film: FilmRef) -> FilmDetails:
        cached = self._cache.get(film.uri)
        if cached is not None:
            return cached

        try:
            response = self._http.get_jina(film.uri)
            details = parse_film_markdown(response.text, film)
        except Exception:
            response = self._http.get(film.uri)
            details = parse_film_html(response.text, film)

        self._cache.put(details)
        return details


def main() -> int:
    shard.FilmResolver = JinaFirstFilmResolver
    return shard.main()


if __name__ == "__main__":
    sys.exit(main())
