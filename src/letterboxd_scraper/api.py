"""High-level Python API for building Letterboxd list datasets."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from letterboxd_scraper.config import (
    DEFAULT_FILTERS,
    AppConfig,
    CacheConfig,
    HttpConfig,
    OutputConfig,
    QueryConfig,
    ValidationConfig,
)
from letterboxd_scraper.exceptions import ConfigurationError
from letterboxd_scraper.pipeline import ScrapePipeline, ScrapeResult


def build_list(
    *,
    seed_lists: Sequence[str],
    include_lists: Sequence[str] = (),
    exclude_lists: Sequence[str] = (),
    filters: Sequence[str] = DEFAULT_FILTERS,
    min_rating: float | None = None,
    max_rating: float | None = None,
    min_rating_inclusive: bool = True,
    max_rating_inclusive: bool = True,
    max_pages_per_list: int = 100,
    concurrency: int = 8,
    timeout_seconds: float = 45.0,
    max_attempts: int = 6,
    min_request_interval_seconds: float = 0.05,
    use_jina_fallback: bool = True,
    cache_enabled: bool = True,
    cache_directory: str | Path = ".cache/letterboxd",
    cache_ttl_hours: float = 24.0,
    expected_min_candidates: int | None = None,
    expected_max_candidates: int | None = None,
    max_unresolved_ratio: float = 0.02,
    require_nonempty_output: bool = True,
    output_directory: str | Path = "output",
    basename: str = "letterboxd_films",
    include_audit_csv: bool = True,
    include_unresolved_json: bool = True,
    include_summary_json: bool = True,
) -> ScrapeResult:
    """Build a validated Letterboxd dataset without requiring a TOML file.

    The function is the recommended entry point for library users. Advanced users
    can construct :class:`~letterboxd_scraper.config.AppConfig` directly and run
    :class:`~letterboxd_scraper.pipeline.ScrapePipeline`.
    """
    normalized_seed_lists = _normalize_urls(seed_lists)
    if not normalized_seed_lists:
        raise ConfigurationError("seed_lists must contain at least one Letterboxd list URL")

    _validate_rating_range(min_rating, max_rating)
    if max_pages_per_list < 1:
        raise ConfigurationError("max_pages_per_list must be positive")
    if concurrency < 1 or max_attempts < 1:
        raise ConfigurationError("concurrency and max_attempts must be positive")
    if not 0 <= max_unresolved_ratio <= 1:
        raise ConfigurationError("max_unresolved_ratio must be between 0 and 1")
    if expected_min_candidates is not None and expected_min_candidates < 0:
        raise ConfigurationError("expected_min_candidates cannot be negative")
    if expected_max_candidates is not None and expected_max_candidates < 0:
        raise ConfigurationError("expected_max_candidates cannot be negative")
    if (
        expected_min_candidates is not None
        and expected_max_candidates is not None
        and expected_min_candidates > expected_max_candidates
    ):
        raise ConfigurationError(
            "expected_min_candidates cannot be greater than expected_max_candidates"
        )
    if not basename.strip():
        raise ConfigurationError("basename cannot be empty")

    config = AppConfig(
        query=QueryConfig(
            seed_lists=normalized_seed_lists,
            include_lists=_normalize_urls(include_lists),
            exclude_lists=_normalize_urls(exclude_lists),
            filters=tuple(item.strip() for item in filters if item.strip()),
            min_rating=min_rating,
            max_rating=max_rating,
            min_rating_inclusive=min_rating_inclusive,
            max_rating_inclusive=max_rating_inclusive,
            max_pages_per_list=max_pages_per_list,
        ),
        http=HttpConfig(
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            concurrency=concurrency,
            min_request_interval_seconds=min_request_interval_seconds,
            use_jina_fallback=use_jina_fallback,
        ),
        cache=CacheConfig(
            enabled=cache_enabled,
            directory=Path(cache_directory),
            ttl_hours=cache_ttl_hours,
        ),
        validation=ValidationConfig(
            expected_min_candidates=expected_min_candidates,
            expected_max_candidates=expected_max_candidates,
            max_unresolved_ratio=max_unresolved_ratio,
            require_nonempty_output=require_nonempty_output,
        ),
        output=OutputConfig(
            directory=Path(output_directory),
            basename=basename.strip(),
            include_audit_csv=include_audit_csv,
            include_unresolved_json=include_unresolved_json,
            include_summary_json=include_summary_json,
        ),
    )
    return ScrapePipeline(config).run()


def _normalize_urls(urls: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(url.strip() for url in urls if url.strip()))


def _validate_rating_range(min_rating: float | None, max_rating: float | None) -> None:
    for name, value in (("min_rating", min_rating), ("max_rating", max_rating)):
        if value is not None and not 0 <= value <= 5:
            raise ConfigurationError(f"{name} must be between 0 and 5")
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise ConfigurationError("min_rating cannot be greater than max_rating")
