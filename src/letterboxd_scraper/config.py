"""Typed configuration loading and validation for TOML scrape definitions."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from letterboxd_scraper.exceptions import ConfigurationError

DEFAULT_FILTERS = ("hide-tv", "hide-shorts", "hide-documentaries")


@dataclass(frozen=True, slots=True)
class QueryConfig:
    seed_lists: tuple[str, ...]
    include_lists: tuple[str, ...] = ()
    exclude_lists: tuple[str, ...] = ()
    filters: tuple[str, ...] = DEFAULT_FILTERS
    min_rating: float | None = None
    max_rating: float | None = None
    min_rating_inclusive: bool = True
    max_rating_inclusive: bool = True
    max_pages_per_list: int = 100


@dataclass(frozen=True, slots=True)
class HttpConfig:
    timeout_seconds: float = 45.0
    max_attempts: int = 6
    backoff_base_seconds: float = 1.3
    max_backoff_seconds: float = 60.0
    concurrency: int = 8
    min_request_interval_seconds: float = 0.05
    use_jina_fallback: bool = True
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )


@dataclass(frozen=True, slots=True)
class CacheConfig:
    enabled: bool = True
    directory: Path = Path(".cache/letterboxd")
    ttl_hours: float = 24.0


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    expected_min_candidates: int | None = None
    expected_max_candidates: int | None = None
    max_unresolved_ratio: float = 0.02
    require_nonempty_output: bool = True


@dataclass(frozen=True, slots=True)
class OutputConfig:
    directory: Path = Path("output")
    basename: str = "letterboxd_films"
    include_audit_csv: bool = True
    include_unresolved_json: bool = True
    include_summary_json: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    query: QueryConfig
    http: HttpConfig = field(default_factory=HttpConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"TOML section [{name}] must be a table")
    return value


def _tuple_of_strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{field_name} must be an array of strings")
    return tuple(item.strip() for item in value if item.strip())


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a TOML configuration file."""
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    query_raw = _section(raw, "query")
    seed_lists = _tuple_of_strings(query_raw.get("seed_lists"), "query.seed_lists")
    if not seed_lists:
        raise ConfigurationError("query.seed_lists must contain at least one Letterboxd list URL")

    filters = (
        _tuple_of_strings(query_raw.get("filters"), "query.filters")
        if "filters" in query_raw
        else DEFAULT_FILTERS
    )
    query = QueryConfig(
        seed_lists=seed_lists,
        include_lists=_tuple_of_strings(query_raw.get("include_lists"), "query.include_lists"),
        exclude_lists=_tuple_of_strings(query_raw.get("exclude_lists"), "query.exclude_lists"),
        filters=filters,
        min_rating=_optional_float(query_raw.get("min_rating"), "query.min_rating"),
        max_rating=_optional_float(query_raw.get("max_rating"), "query.max_rating"),
        min_rating_inclusive=bool(query_raw.get("min_rating_inclusive", True)),
        max_rating_inclusive=bool(query_raw.get("max_rating_inclusive", True)),
        max_pages_per_list=int(query_raw.get("max_pages_per_list", 100)),
    )
    if query.min_rating is not None and not 0 <= query.min_rating <= 5:
        raise ConfigurationError("query.min_rating must be between 0 and 5")
    if query.max_rating is not None and not 0 <= query.max_rating <= 5:
        raise ConfigurationError("query.max_rating must be between 0 and 5")
    if (
        query.min_rating is not None
        and query.max_rating is not None
        and query.min_rating > query.max_rating
    ):
        raise ConfigurationError("query.min_rating cannot be greater than query.max_rating")
    if query.max_pages_per_list < 1:
        raise ConfigurationError("query.max_pages_per_list must be positive")

    http_raw = _section(raw, "http")
    http = HttpConfig(
        timeout_seconds=float(http_raw.get("timeout_seconds", 45.0)),
        max_attempts=int(http_raw.get("max_attempts", 6)),
        backoff_base_seconds=float(http_raw.get("backoff_base_seconds", 1.3)),
        max_backoff_seconds=float(http_raw.get("max_backoff_seconds", 60.0)),
        concurrency=int(http_raw.get("concurrency", 8)),
        min_request_interval_seconds=float(http_raw.get("min_request_interval_seconds", 0.05)),
        use_jina_fallback=bool(http_raw.get("use_jina_fallback", True)),
        user_agent=str(http_raw.get("user_agent", HttpConfig().user_agent)),
    )
    if http.max_attempts < 1 or http.concurrency < 1:
        raise ConfigurationError("http.max_attempts and http.concurrency must be positive")

    cache_raw = _section(raw, "cache")
    cache = CacheConfig(
        enabled=bool(cache_raw.get("enabled", True)),
        directory=Path(cache_raw.get("directory", ".cache/letterboxd")),
        ttl_hours=float(cache_raw.get("ttl_hours", 24.0)),
    )

    validation_raw = _section(raw, "validation")
    validation = ValidationConfig(
        expected_min_candidates=_optional_int(
            validation_raw.get("expected_min_candidates"),
            "validation.expected_min_candidates",
        ),
        expected_max_candidates=_optional_int(
            validation_raw.get("expected_max_candidates"),
            "validation.expected_max_candidates",
        ),
        max_unresolved_ratio=float(validation_raw.get("max_unresolved_ratio", 0.02)),
        require_nonempty_output=bool(validation_raw.get("require_nonempty_output", True)),
    )
    if not 0 <= validation.max_unresolved_ratio <= 1:
        raise ConfigurationError("validation.max_unresolved_ratio must be between 0 and 1")

    output_raw = _section(raw, "output")
    output = OutputConfig(
        directory=Path(output_raw.get("directory", "output")),
        basename=str(output_raw.get("basename", "letterboxd_films")),
        include_audit_csv=bool(output_raw.get("include_audit_csv", True)),
        include_unresolved_json=bool(output_raw.get("include_unresolved_json", True)),
        include_summary_json=bool(output_raw.get("include_summary_json", True)),
    )
    if not output.basename.strip():
        raise ConfigurationError("output.basename cannot be empty")

    return AppConfig(
        query=query,
        http=http,
        cache=cache,
        validation=validation,
        output=output,
    )


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} must be numeric") from exc


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} must be an integer") from exc
