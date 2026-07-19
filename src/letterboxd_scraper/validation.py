"""Safety checks that prevent empty or obviously incomplete exports."""

from __future__ import annotations

from letterboxd_scraper.config import ValidationConfig
from letterboxd_scraper.exceptions import ValidationError
from letterboxd_scraper.models import FilmDetails, FilmRef


def validate_candidates(candidates: dict[str, FilmRef], config: ValidationConfig) -> None:
    count = len(candidates)
    if config.expected_min_candidates is not None and count < config.expected_min_candidates:
        raise ValidationError(
            f"Candidate scrape is incomplete: found {count}, expected at least "
            f"{config.expected_min_candidates}"
        )
    if config.expected_max_candidates is not None and count > config.expected_max_candidates:
        raise ValidationError(
            f"Candidate scrape is suspiciously large: found {count}, expected at most "
            f"{config.expected_max_candidates}"
        )


def validate_resolution(
    resolved: list[FilmDetails],
    unresolved: list[FilmDetails],
    config: ValidationConfig,
) -> None:
    total = len(resolved) + len(unresolved)
    if total == 0:
        raise ValidationError("No films were resolved")
    unresolved_ratio = len(unresolved) / total
    if unresolved_ratio > config.max_unresolved_ratio:
        raise ValidationError(
            f"Unresolved ratio {unresolved_ratio:.2%} exceeds configured maximum "
            f"{config.max_unresolved_ratio:.2%}"
        )


def validate_selected(selected: list[FilmDetails], config: ValidationConfig) -> None:
    if config.require_nonempty_output and not selected:
        raise ValidationError("Rating filters produced an empty dataset")
    uris = [film.uri for film in selected]
    if len(uris) != len(set(uris)):
        raise ValidationError("Selected output contains duplicate Letterboxd URIs")
