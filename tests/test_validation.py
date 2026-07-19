import pytest

from letterboxd_scraper.config import ValidationConfig
from letterboxd_scraper.exceptions import ValidationError
from letterboxd_scraper.models import FilmDetails, FilmRef
from letterboxd_scraper.validation import (
    validate_candidates,
    validate_resolution,
    validate_selected,
)


def complete(slug: str) -> FilmDetails:
    return FilmDetails(
        uri=f"https://letterboxd.com/film/{slug}/",
        title=slug,
        year=2020,
        average_rating=3.5,
    )


def test_candidate_count_guards() -> None:
    candidates = {"a": FilmRef(uri="a")}
    with pytest.raises(ValidationError, match="expected at least"):
        validate_candidates(candidates, ValidationConfig(expected_min_candidates=2))
    with pytest.raises(ValidationError, match="expected at most"):
        validate_candidates(candidates, ValidationConfig(expected_max_candidates=0))


def test_resolution_rejects_empty_and_excessive_unresolved() -> None:
    with pytest.raises(ValidationError, match="No films"):
        validate_resolution([], [], ValidationConfig())

    unresolved = [
        FilmDetails(uri="a", title="", year=None, average_rating=None),
        FilmDetails(uri="b", title="", year=None, average_rating=None),
    ]
    with pytest.raises(ValidationError, match="Unresolved ratio"):
        validate_resolution(
            [complete("ok")], unresolved, ValidationConfig(max_unresolved_ratio=0.5)
        )


def test_selected_rejects_empty_and_duplicates() -> None:
    with pytest.raises(ValidationError, match="empty dataset"):
        validate_selected([], ValidationConfig(require_nonempty_output=True))

    duplicate = complete("same")
    with pytest.raises(ValidationError, match="duplicate"):
        validate_selected([duplicate, duplicate], ValidationConfig())
