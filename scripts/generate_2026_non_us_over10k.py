from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urljoin

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig, HttpConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.list_scraper import ListScraper

BASE_LIST = "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/"
YEAR = 2026
MIN_RATING = 3.4
OUTPUT_DIR = Path("output/2026-non-us-over10k-rating-above-3-4")
IMPORT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4.csv"
AUDIT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4_audit.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
DIAGNOSTICS_JSON = OUTPUT_DIR / "diagnostics.json"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    year_url = urljoin(BASE_LIST, f"year/{YEAR}/")
    usa_url_candidates = [
        urljoin(BASE_LIST, f"country/usa/year/{YEAR}/"),
        urljoin(BASE_LIST, f"year/{YEAR}/country/usa/"),
        urljoin(BASE_LIST, "country/usa/"),
    ]

    http = HttpClient(
        HttpConfig(
            timeout_seconds=45,
            max_attempts=6,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=8,
            min_request_interval_seconds=0.08,
            use_jina_fallback=True,
        )
    )
    scraper = ListScraper(http, filters=(), max_pages=100)
    resolver = FilmResolver(
        http,
        FilmCache(
            CacheConfig(
                enabled=True,
                directory=Path(".cache/letterboxd-2026-non-us"),
                ttl_hours=24,
            )
        ),
        concurrency=8,
    )

    diagnostics: dict[str, object] = {
        "year_url": year_url,
        "usa_url_attempts": [],
    }

    print(f"Scraping 2026 source: {year_url}", flush=True)
    year_result = scraper.scrape(year_url)
    year_films = year_result.films
    year_uris = set(year_films)
    known_ref_years = [ref.year for ref in year_films.values() if ref.year is not None]
    year_match_ratio = (
        sum(1 for value in known_ref_years if value == YEAR) / len(known_ref_years)
        if known_ref_years
        else None
    )
    diagnostics["year_candidate_count"] = len(year_uris)
    diagnostics["year_known_ref_count"] = len(known_ref_years)
    diagnostics["year_match_ratio"] = year_match_ratio
    diagnostics["year_source_counts"] = year_result.source_counts

    if not year_uris:
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError("The 2026 source list returned no films.")
    if year_match_ratio is not None and year_match_ratio < 0.80:
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError(
            f"The year-filtered list looks malformed: only {year_match_ratio:.1%} "
            f"of references with a known year are from {YEAR}."
        )

    usa_uris: set[str] | None = None
    usa_exclusion_url = ""
    for candidate_url in usa_url_candidates:
        print(f"Trying USA exclusion source: {candidate_url}", flush=True)
        try:
            result = scraper.scrape(candidate_url)
            raw_uris = set(result.films)
            intersection = raw_uris & year_uris
            attempt = {
                "url": candidate_url,
                "raw_count": len(raw_uris),
                "year_intersection_count": len(intersection),
                "source_counts": result.source_counts,
                "error": "",
            }
            cast_attempts = diagnostics["usa_url_attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(attempt)
            if 0 < len(intersection) < len(year_uris):
                usa_uris = intersection
                usa_exclusion_url = candidate_url
                break
        except Exception as exc:
            cast_attempts = diagnostics["usa_url_attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(
                {
                    "url": candidate_url,
                    "raw_count": 0,
                    "year_intersection_count": 0,
                    "source_counts": {},
                    "error": repr(exc),
                }
            )

    if not usa_uris:
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError("Could not obtain a valid USA exclusion subset from any filter URL.")

    diagnostics["selected_usa_exclusion_url"] = usa_exclusion_url
    diagnostics["usa_excluded_count"] = len(usa_uris)
    DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")

    candidates = {uri: ref for uri, ref in year_films.items() if uri not in usa_uris}
    print(
        f"Candidates: {len(year_uris)} total in 2026, "
        f"{len(usa_uris)} excluded as USA, {len(candidates)} remaining.",
        flush=True,
    )

    resolved, unresolved = resolver.resolve_many(candidates)
    selected = [
        item
        for item in resolved
        if item.year == YEAR
        and item.average_rating is not None
        and item.average_rating > MIN_RATING
        and item.uri not in usa_uris
    ]
    selected.sort(
        key=lambda item: (
            -(item.average_rating or 0),
            (item.title or "").casefold(),
            item.uri,
        )
    )

    if not selected:
        raise RuntimeError("No films matched the requested criteria.")
    if len({item.uri for item in selected}) != len(selected):
        raise RuntimeError("Duplicate canonical Letterboxd URIs found in the selected output.")
    if any(item.year != YEAR for item in selected):
        raise RuntimeError("A selected film does not have release year 2026.")
    if any(item.average_rating is None or item.average_rating <= MIN_RATING for item in selected):
        raise RuntimeError("A selected film does not satisfy rating > 3.4.")
    if any(item.uri in usa_uris for item in selected):
        raise RuntimeError("A USA-filtered film leaked into the final selection.")

    import_rows = [
        {
            "Title": item.title,
            "Year": item.year,
            "LetterboxdURI": item.uri,
        }
        for item in selected
    ]
    audit_rows = [
        {
            "Rank": rank,
            "Title": item.title,
            "Year": item.year,
            "LetterboxdURI": item.uri,
            "AverageRating": f"{item.average_rating:.2f}",
            "RatingSource": item.rating_source,
            "MetadataSource": item.metadata_source,
            "WatchThresholdSource": BASE_LIST,
            "YearFilterURL": year_url,
            "USAExclusionURL": usa_exclusion_url,
            "Error": item.error,
        }
        for rank, item in enumerate(selected, 1)
    ]

    write_csv(IMPORT_CSV, ["Title", "Year", "LetterboxdURI"], import_rows)
    write_csv(
        AUDIT_CSV,
        [
            "Rank",
            "Title",
            "Year",
            "LetterboxdURI",
            "AverageRating",
            "RatingSource",
            "MetadataSource",
            "WatchThresholdSource",
            "YearFilterURL",
            "USAExclusionURL",
            "Error",
        ],
        audit_rows,
    )

    summary = {
        "criteria": {
            "year": YEAR,
            "minimum_watches": 10000,
            "rating_strictly_greater_than": MIN_RATING,
            "exclude_any_film_in_usa_country_filter": True,
        },
        "source_list": BASE_LIST,
        "year_filter_url": year_url,
        "usa_exclusion_url": usa_exclusion_url,
        "counts": {
            "year_candidates": len(year_uris),
            "usa_excluded": len(usa_uris),
            "non_usa_candidates": len(candidates),
            "resolved": len(resolved),
            "unresolved": len(unresolved),
            "selected": len(selected),
        },
        "unresolved": [
            {
                "Title": item.title,
                "Year": item.year,
                "LetterboxdURI": item.uri,
                "Error": item.error,
            }
            for item in unresolved
        ],
        "outputs": {
            "import_csv": str(IMPORT_CSV),
            "audit_csv": str(AUDIT_CSV),
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary["counts"], indent=2), flush=True)
    print(f"Wrote {IMPORT_CSV}", flush=True)
    print(f"Wrote {AUDIT_CSV}", flush=True)


if __name__ == "__main__":
    main()
