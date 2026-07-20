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


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    year_url = urljoin(BASE_LIST, f"year/{YEAR}/")
    usa_year_url = urljoin(BASE_LIST, f"country/usa/year/{YEAR}/")

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
    scraper = ListScraper(http, filters=(), max_pages=25)
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

    print(f"Scraping 2026 source: {year_url}", flush=True)
    year_result = scraper.scrape(year_url)
    print(f"Scraping 2026 USA exclusion: {usa_year_url}", flush=True)
    usa_result = scraper.scrape(usa_year_url)

    year_films = year_result.films
    usa_films = usa_result.films
    year_uris = set(year_films)
    usa_uris = set(usa_films)

    if not year_uris:
        raise RuntimeError("The 2026 source list returned no films.")
    if not usa_uris:
        raise RuntimeError("The USA exclusion list returned no films; the filter URL may be invalid.")
    if not usa_uris.issubset(year_uris):
        unexpected = sorted(usa_uris - year_uris)[:10]
        raise RuntimeError(
            "The USA-filtered list is not a subset of the 2026 list. "
            f"Unexpected examples: {unexpected}"
        )

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
            "USAExclusionURL": usa_year_url,
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
            "exclude_country_filter": "USA",
        },
        "source_list": BASE_LIST,
        "year_filter_url": year_url,
        "usa_exclusion_url": usa_year_url,
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
