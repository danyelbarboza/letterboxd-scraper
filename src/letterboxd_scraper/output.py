"""Deterministic Letterboxd import, audit, unresolved, and summary exports."""

from __future__ import annotations

import csv
import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from letterboxd_scraper.config import AppConfig
from letterboxd_scraper.models import FilmDetails, ListScrapeResult


@dataclass(frozen=True, slots=True)
class OutputPaths:
    import_csv: Path
    audit_csv: Path | None
    unresolved_json: Path | None
    summary_json: Path | None


def write_outputs(
    selected: list[FilmDetails],
    unresolved: list[FilmDetails],
    list_results: list[ListScrapeResult],
    config: AppConfig,
) -> OutputPaths:
    directory = config.output.directory
    directory.mkdir(parents=True, exist_ok=True)
    basename = config.output.basename

    import_csv = directory / f"{basename}.csv"
    _write_import_csv(import_csv, selected)

    audit_csv = directory / f"{basename}_audit.csv" if config.output.include_audit_csv else None
    if audit_csv:
        _write_audit_csv(audit_csv, selected)

    unresolved_json = (
        directory / f"{basename}_unresolved.json" if config.output.include_unresolved_json else None
    )
    if unresolved_json:
        unresolved_json.write_text(
            json.dumps([film.to_dict() for film in unresolved], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_json = (
        directory / f"{basename}_summary.json" if config.output.include_summary_json else None
    )
    if summary_json:
        summary_json.write_text(
            json.dumps(
                build_summary(selected, unresolved, list_results, config),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return OutputPaths(import_csv, audit_csv, unresolved_json, summary_json)


def build_summary(
    selected: list[FilmDetails],
    unresolved: list[FilmDetails],
    list_results: list[ListScrapeResult],
    config: AppConfig,
) -> dict[str, object]:
    ratings = [film.average_rating for film in selected if film.average_rating is not None]
    years = [film.year for film in selected if film.year is not None]
    exact_distribution = Counter(f"{rating:.2f}" for rating in ratings)
    tenth_distribution = Counter(_rating_tenth_bucket(rating) for rating in ratings)
    return {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_rows": len(selected),
        "unique_uris": len({film.uri for film in selected}),
        "unresolved_rows": len(unresolved),
        "minimum_selected_rating": min(ratings, default=None),
        "maximum_selected_rating": max(ratings, default=None),
        "earliest_year": min(years, default=None),
        "latest_year": max(years, default=None),
        "rating_distribution_exact": dict(sorted(exact_distribution.items())),
        "rating_distribution_by_tenth": dict(sorted(tenth_distribution.items())),
        "query": {
            "seed_lists": list(config.query.seed_lists),
            "include_lists": list(config.query.include_lists),
            "exclude_lists": list(config.query.exclude_lists),
            "filters": list(config.query.filters),
            "min_rating": config.query.min_rating,
            "max_rating": config.query.max_rating,
            "min_rating_inclusive": config.query.min_rating_inclusive,
            "max_rating_inclusive": config.query.max_rating_inclusive,
        },
        "list_scrapes": [
            {
                "url": result.list_url,
                "films": len(result.films),
                "pages_read": result.pages_read,
                "source_counts": result.source_counts,
            }
            for result in list_results
        ],
    }


def _write_import_csv(path: Path, films: list[FilmDetails]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Title", "Year", "LetterboxdURI"])
        writer.writeheader()
        for film in films:
            writer.writerow({"Title": film.title, "Year": film.year, "LetterboxdURI": film.uri})


def _write_audit_csv(path: Path, films: list[FilmDetails]) -> None:
    fields = [
        "Title",
        "Year",
        "LetterboxdURI",
        "AverageRating",
        "RatingSource",
        "MetadataSource",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for film in films:
            writer.writerow(
                {
                    "Title": film.title,
                    "Year": film.year,
                    "LetterboxdURI": film.uri,
                    "AverageRating": (
                        f"{film.average_rating:.2f}" if film.average_rating is not None else ""
                    ),
                    "RatingSource": film.rating_source,
                    "MetadataSource": film.metadata_source,
                }
            )


def _rating_tenth_bucket(rating: float) -> str:
    lower = int(rating * 10) / 10
    upper = lower + 0.09
    return f"{lower:.2f}-{upper:.2f}"
