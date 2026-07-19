"""Run a bounded live smoke test and produce example Letterboxd CSV files.

The run intentionally reads only the first three pages of a public popularity list.
This keeps the test suitable for CI while still exercising live pagination, metadata
resolution, caching, rating boundaries, validation, and package exporters.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from letterboxd_scraper import __version__, build_list, write_audit_csv, write_letterboxd_csv
from letterboxd_scraper.models import FilmDetails

SOURCE_LIST = "https://letterboxd.com/cinemageekyt/list/letterboxd-500k-watched-club/"
OUTPUT_ROOT = Path("live-test-output")
CACHE_DIRECTORY = Path(".cache/live-library-smoke")
MAX_PAGES = 3


@dataclass(frozen=True, slots=True)
class Criterion:
    slug: str
    description: str
    predicate: Callable[[FilmDetails], bool]
    api_arguments: dict[str, float | bool]


CRITERIA = (
    Criterion(
        slug="rating_below_3_5",
        description="Average rating strictly below 3.50",
        predicate=lambda film: film.average_rating is not None and film.average_rating < 3.5,
        api_arguments={"max_rating": 3.5, "max_rating_inclusive": False},
    ),
    Criterion(
        slug="rating_3_5_to_4_0",
        description="Average rating from 3.50 inclusive to 4.00 exclusive",
        predicate=lambda film: (
            film.average_rating is not None and 3.5 <= film.average_rating < 4.0
        ),
        api_arguments={
            "min_rating": 3.5,
            "max_rating": 4.0,
            "min_rating_inclusive": True,
            "max_rating_inclusive": False,
        },
    ),
    Criterion(
        slug="rating_4_0_and_above",
        description="Average rating greater than or equal to 4.00",
        predicate=lambda film: film.average_rating is not None and film.average_rating >= 4.0,
        api_arguments={"min_rating": 4.0, "min_rating_inclusive": True},
    ),
)


def _common_arguments(output_directory: Path, basename: str) -> dict[str, object]:
    return {
        "seed_lists": [SOURCE_LIST],
        "filters": ["hide-tv", "hide-shorts", "hide-documentaries"],
        "max_pages_per_list": MAX_PAGES,
        "concurrency": 10,
        "timeout_seconds": 45.0,
        "max_attempts": 5,
        "min_request_interval_seconds": 0.08,
        "use_jina_fallback": True,
        "cache_enabled": True,
        "cache_directory": CACHE_DIRECTORY,
        "cache_ttl_hours": 24.0,
        "max_unresolved_ratio": 0.20,
        "require_nonempty_output": True,
        "output_directory": output_directory,
        "basename": basename,
        "include_audit_csv": True,
        "include_unresolved_json": True,
        "include_summary_json": True,
    }


def _uri_set(films: list[FilmDetails]) -> set[str]:
    return {film.uri for film in films}


def _validate_export(path: Path, expected_rows: int) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    expected_header = ["Title", "Year", "LetterboxdURI"]
    if list(rows[0].keys()) != expected_header if rows else expected_rows != 0:
        raise AssertionError(f"Unexpected CSV columns in {path}")
    if len(rows) != expected_rows:
        raise AssertionError(f"{path} contains {len(rows)} rows; expected {expected_rows}")

    uris = [row["LetterboxdURI"] for row in rows]
    if len(uris) != len(set(uris)):
        raise AssertionError(f"Duplicate LetterboxdURI values found in {path}")
    if any(not uri.startswith("https://letterboxd.com/film/") for uri in uris):
        raise AssertionError(f"Non-canonical Letterboxd URI found in {path}")


def main() -> None:
    started = time.time()
    shutil.rmtree(OUTPUT_ROOT, ignore_errors=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    base = build_list(**_common_arguments(OUTPUT_ROOT / "base", "sample_all_resolved"))
    if len(base.candidates) < 100:
        raise AssertionError(
            f"Live sample returned only {len(base.candidates)} candidates; expected at least 100"
        )
    if len(base.resolved) < 80:
        raise AssertionError(
            f"Live sample resolved only {len(base.resolved)} films; expected at least 80"
        )

    clean_directory = OUTPUT_ROOT / "csv"
    clean_directory.mkdir(parents=True, exist_ok=True)
    write_letterboxd_csv(clean_directory / "sample_all_resolved.csv", base.resolved)
    write_audit_csv(clean_directory / "sample_all_resolved_audit.csv", base.resolved)
    _validate_export(clean_directory / "sample_all_resolved.csv", len(base.resolved))

    summaries: list[dict[str, object]] = []
    for criterion in CRITERIA:
        result = build_list(
            **_common_arguments(OUTPUT_ROOT / criterion.slug, criterion.slug),
            **criterion.api_arguments,
        )
        expected = [film for film in base.resolved if criterion.predicate(film)]
        if _uri_set(result.selected) != _uri_set(expected):
            raise AssertionError(f"API rating boundaries disagreed for {criterion.slug}")
        if not result.selected:
            raise AssertionError(f"Criterion {criterion.slug} unexpectedly produced no rows")

        import_path = clean_directory / f"{criterion.slug}.csv"
        audit_path = clean_directory / f"{criterion.slug}_audit.csv"
        write_letterboxd_csv(import_path, result.selected)
        write_audit_csv(audit_path, result.selected)
        _validate_export(import_path, len(result.selected))

        ratings = [film.average_rating for film in result.selected if film.average_rating is not None]
        summaries.append(
            {
                "file": import_path.name,
                "criterion": criterion.description,
                "rows": len(result.selected),
                "minimum_rating": min(ratings, default=None),
                "maximum_rating": max(ratings, default=None),
            }
        )

    summary_path = clean_directory / "csv_test_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "file",
                "criterion",
                "rows",
                "minimum_rating",
                "maximum_rating",
            ],
        )
        writer.writeheader()
        writer.writerows(summaries)

    report = {
        "library_version": __version__,
        "source_list": SOURCE_LIST,
        "sample_pages": MAX_PAGES,
        "candidate_count": len(base.candidates),
        "resolved_count": len(base.resolved),
        "unresolved_count": len(base.unresolved),
        "criteria": summaries,
        "duration_seconds": round(time.time() - started, 2),
        "checks": [
            "live list pagination",
            "live film metadata resolution",
            "shared metadata cache",
            "inclusive and exclusive rating boundaries",
            "API result equivalence against manual predicates",
            "Letterboxd import CSV schema",
            "canonical URI validation",
            "duplicate URI validation",
            "audit CSV export",
        ],
    }
    (OUTPUT_ROOT / "live_test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
