"""Immutable domain models used across scraping, filtering, and export."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class FilmRef:
    """A canonical Letterboxd film reference, possibly with partial metadata."""

    uri: str
    title: str = ""
    year: int | None = None

    def merge(self, other: FilmRef) -> FilmRef:
        """Prefer populated metadata while preserving the canonical URI."""
        if self.uri != other.uri:
            raise ValueError("Cannot merge film references with different URIs")
        return replace(
            self,
            title=self.title or other.title,
            year=self.year or other.year,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"Title": self.title, "Year": self.year, "LetterboxdURI": self.uri}


@dataclass(frozen=True, slots=True)
class FilmDetails:
    """Resolved film metadata and current Letterboxd average rating."""

    uri: str
    title: str
    year: int | None
    average_rating: float | None
    rating_source: str = ""
    metadata_source: str = ""
    error: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.title and self.year and self.average_rating is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "Title": self.title,
            "Year": self.year,
            "LetterboxdURI": self.uri,
            "AverageRating": self.average_rating,
            "RatingSource": self.rating_source,
            "MetadataSource": self.metadata_source,
            "Error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ListScrapeResult:
    """Films discovered from one Letterboxd list and diagnostics about the run."""

    list_url: str
    films: dict[str, FilmRef]
    pages_read: int
    source_counts: dict[str, int]
