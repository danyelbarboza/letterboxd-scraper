"""Application pipeline coordinating list algebra, resolution, filtering, and export."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import AppConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.list_scraper import ListScraper
from letterboxd_scraper.models import FilmDetails, FilmRef, ListScrapeResult
from letterboxd_scraper.output import OutputPaths, write_outputs
from letterboxd_scraper.validation import (
    validate_candidates,
    validate_resolution,
    validate_selected,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScrapeResult:
    """Complete in-memory and on-disk result of one configured pipeline run."""

    candidates: dict[str, FilmRef]
    resolved: list[FilmDetails]
    unresolved: list[FilmDetails]
    selected: list[FilmDetails]
    list_results: list[ListScrapeResult]
    output_paths: OutputPaths


class ScrapePipeline:
    """Build a reproducible Letterboxd dataset from an :class:`AppConfig`."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._http = HttpClient(config.http)
        self._list_scraper = ListScraper(
            self._http,
            filters=config.query.filters,
            max_pages=config.query.max_pages_per_list,
        )
        self._resolver = FilmResolver(
            self._http,
            FilmCache(config.cache),
            concurrency=config.http.concurrency,
        )

    def run(self) -> ScrapeResult:
        list_results_by_url: dict[str, ListScrapeResult] = {}
        all_urls = tuple(
            dict.fromkeys(
                (
                    *self._config.query.seed_lists,
                    *self._config.query.include_lists,
                    *self._config.query.exclude_lists,
                )
            )
        )

        for index, url in enumerate(all_urls, start=1):
            logger.info("Scraping list %s/%s: %s", index, len(all_urls), url)
            result = self._list_scraper.scrape(url)
            logger.info(
                "List returned %s films across %s pages via %s",
                len(result.films),
                result.pages_read,
                result.source_counts,
            )
            list_results_by_url[url] = result

        candidates = apply_list_algebra(
            seed=[list_results_by_url[url].films for url in self._config.query.seed_lists],
            include=[list_results_by_url[url].films for url in self._config.query.include_lists],
            exclude=[list_results_by_url[url].films for url in self._config.query.exclude_lists],
        )
        logger.info("List algebra produced %s unique candidates", len(candidates))
        validate_candidates(candidates, self._config.validation)

        resolved, unresolved = self._resolver.resolve_many(candidates)
        logger.info("Resolved %s films; %s remain unresolved", len(resolved), len(unresolved))
        validate_resolution(resolved, unresolved, self._config.validation)

        selected = filter_by_rating(resolved, self._config)
        selected.sort(key=_selected_sort_key)
        logger.info("Rating filters selected %s films", len(selected))
        validate_selected(selected, self._config.validation)

        list_results = [list_results_by_url[url] for url in all_urls]
        output_paths = write_outputs(
            selected=selected,
            unresolved=unresolved,
            list_results=list_results,
            config=self._config,
        )
        return ScrapeResult(
            candidates=candidates,
            resolved=resolved,
            unresolved=unresolved,
            selected=selected,
            list_results=list_results,
            output_paths=output_paths,
        )


def apply_list_algebra(
    *,
    seed: list[dict[str, FilmRef]],
    include: list[dict[str, FilmRef]],
    exclude: list[dict[str, FilmRef]],
) -> dict[str, FilmRef]:
    """Apply ``union(seed) ∩ include[0] ∩ ... - union(exclude)``.

    This model reproduces watch-count bands using community-maintained threshold
    lists. For example, ``all narrative films ∩ over-10k - over-100k`` yields a
    10k-to-100k snapshot without needing a private Letterboxd API.
    """
    candidates: dict[str, FilmRef] = {}
    for source in seed:
        for uri, film in source.items():
            candidates[uri] = candidates[uri].merge(film) if uri in candidates else film

    for source in include:
        shared = candidates.keys() & source.keys()
        candidates = {uri: candidates[uri].merge(source[uri]) for uri in shared}

    excluded_uris: set[str] = set()
    for source in exclude:
        excluded_uris.update(source)
    return {uri: film for uri, film in candidates.items() if uri not in excluded_uris}


def filter_by_rating(films: list[FilmDetails], config: AppConfig) -> list[FilmDetails]:
    """Apply configured inclusive or exclusive rating boundaries."""
    selected: list[FilmDetails] = []
    for film in films:
        rating = film.average_rating
        if rating is None:
            continue
        if config.query.min_rating is not None:
            if config.query.min_rating_inclusive:
                if rating < config.query.min_rating:
                    continue
            elif rating <= config.query.min_rating:
                continue
        if config.query.max_rating is not None:
            if config.query.max_rating_inclusive:
                if rating > config.query.max_rating:
                    continue
            elif rating >= config.query.max_rating:
                continue
        selected.append(film)
    return selected


def _selected_sort_key(film: FilmDetails) -> tuple[float, str, int, str]:
    return (
        -(film.average_rating or 0.0),
        film.title.casefold(),
        film.year or 0,
        film.uri,
    )
