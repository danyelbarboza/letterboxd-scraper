from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig, HttpConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.list_scraper import ListScraper
from letterboxd_scraper.models import FilmRef

BASE_LIST = "https://letterboxd.com/hershwin/list/all-the-movies/"
YEAR = 2026
MIN_WATCHES_EXCLUSIVE = 10_000
MIN_RATING_EXCLUSIVE = 3.4
MEMBERS_PER_PAGE = 25
THRESHOLD_PAGE = MIN_WATCHES_EXCLUSIVE // MEMBERS_PER_PAGE + 1
OUTPUT_DIR = Path("output/2026-non-us-over10k-rating-above-3-4")
IMPORT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4.csv"
AUDIT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4_audit.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
DIAGNOSTICS_JSON = OUTPUT_DIR / "diagnostics.json"
PAGE_SIZE_VALIDATION_FILM = "https://letterboxd.com/film/project-hail-mary/"
_ACTIVITY_PATTERN = re.compile(r"^Activity for film\b", flags=re.MULTILINE)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def member_rows(markdown: str) -> int:
    return len(_ACTIVITY_PATTERN.findall(markdown))


def validate_members_page_size(http: HttpClient) -> None:
    url = urljoin(PAGE_SIZE_VALIDATION_FILM, "members/")
    response = http.get_jina(url)
    count = member_rows(response.text)
    if count != MEMBERS_PER_PAGE:
        raise RuntimeError(
            f"Expected {MEMBERS_PER_PAGE} member rows on a full Letterboxd members page, "
            f"but parsed {count} from {url}. Refusing to infer the 10k threshold."
        )


def watch_threshold_url(ref: FilmRef) -> str:
    return urljoin(ref.uri, f"members/page/{THRESHOLD_PAGE}/")


def check_over_10k(http: HttpClient, ref: FilmRef) -> tuple[str, bool | None, str]:
    url = watch_threshold_url(ref)
    try:
        response = http.get_jina(url)
    except Exception as exc:
        return ref.uri, None, repr(exc)
    return ref.uri, member_rows(response.text) > 0, ""


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    year_url = urljoin(BASE_LIST, f"year/{YEAR}/")
    usa_url_candidates = [
        urljoin(BASE_LIST, f"country/usa/year/{YEAR}/"),
        urljoin(BASE_LIST, f"year/{YEAR}/country/usa/"),
        urljoin(BASE_LIST, "country/usa/"),
    ]

    list_http = HttpClient(
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
    watch_http = HttpClient(
        HttpConfig(
            timeout_seconds=45,
            max_attempts=4,
            backoff_base_seconds=1,
            max_backoff_seconds=15,
            concurrency=6,
            min_request_interval_seconds=0.12,
            use_jina_fallback=True,
        )
    )
    scraper = ListScraper(list_http, filters=(), max_pages=100)
    resolver = FilmResolver(
        list_http,
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
        "source_list": BASE_LIST,
        "year_url": year_url,
        "usa_url_attempts": [],
        "members_per_page": MEMBERS_PER_PAGE,
        "watch_threshold_page": THRESHOLD_PAGE,
    }

    print(f"Scraping complete movie source filtered to {YEAR}: {year_url}", flush=True)
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
            attempts = diagnostics["usa_url_attempts"]
            assert isinstance(attempts, list)
            attempts.append(
                {
                    "url": candidate_url,
                    "raw_count": len(raw_uris),
                    "year_intersection_count": len(intersection),
                    "source_counts": result.source_counts,
                    "error": "",
                }
            )
            if 0 < len(intersection) < len(year_uris):
                usa_uris = intersection
                usa_exclusion_url = candidate_url
                break
        except Exception as exc:
            attempts = diagnostics["usa_url_attempts"]
            assert isinstance(attempts, list)
            attempts.append(
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

    non_usa_candidates = {
        uri: ref for uri, ref in year_films.items() if uri not in usa_uris
    }
    print(
        f"Year candidates: {len(year_uris)}; USA-tagged exclusions: {len(usa_uris)}; "
        f"non-USA candidates: {len(non_usa_candidates)}.",
        flush=True,
    )

    print("Validating Letterboxd members-page size before applying the 10k cutoff.", flush=True)
    validate_members_page_size(watch_http)

    over_10k_uris: set[str] = set()
    watch_check_errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(check_over_10k, watch_http, ref): ref
            for ref in non_usa_candidates.values()
        }
        for index, future in enumerate(as_completed(futures), 1):
            ref = futures[future]
            try:
                uri, passes, error = future.result()
            except Exception as exc:
                uri, passes, error = ref.uri, None, repr(exc)
            if passes is True:
                over_10k_uris.add(uri)
            elif passes is None:
                watch_check_errors.append(
                    {
                        "Title": ref.title,
                        "Year": str(ref.year or ""),
                        "LetterboxdURI": ref.uri,
                        "ThresholdURL": watch_threshold_url(ref),
                        "Error": error,
                    }
                )
            if index % 25 == 0 or index == len(futures):
                print(
                    f"Watch threshold checked {index}/{len(futures)}; "
                    f"passing={len(over_10k_uris)}, errors={len(watch_check_errors)}.",
                    flush=True,
                )

    if watch_check_errors:
        diagnostics["watch_check_errors"] = watch_check_errors
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError(
            f"Could not verify the >10k watch threshold for {len(watch_check_errors)} films; "
            "refusing to produce a potentially incomplete CSV."
        )

    over_10k_candidates = {
        uri: non_usa_candidates[uri]
        for uri in sorted(over_10k_uris)
    }
    diagnostics["over_10k_candidate_count"] = len(over_10k_candidates)
    if not over_10k_candidates:
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError("No non-USA 2026 films were verified above 10,000 watches.")

    print(f"Resolving ratings for {len(over_10k_candidates)} verified films.", flush=True)
    resolved, unresolved = resolver.resolve_many(over_10k_candidates)
    selected = [
        item
        for item in resolved
        if item.year == YEAR
        and item.average_rating is not None
        and item.average_rating > MIN_RATING_EXCLUSIVE
        and item.uri not in usa_uris
        and item.uri in over_10k_uris
    ]
    selected.sort(
        key=lambda item: (
            -(item.average_rating or 0),
            (item.title or "").casefold(),
            item.uri,
        )
    )

    if unresolved:
        diagnostics["metadata_unresolved"] = [
            {
                "Title": item.title,
                "Year": item.year,
                "LetterboxdURI": item.uri,
                "Error": item.error,
            }
            for item in unresolved
        ]
        DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
        raise RuntimeError(
            f"Could not resolve complete metadata for {len(unresolved)} verified films; "
            "refusing to produce a potentially incomplete CSV."
        )
    if not selected:
        raise RuntimeError("No films matched every requested criterion.")
    if len({item.uri for item in selected}) != len(selected):
        raise RuntimeError("Duplicate canonical Letterboxd URIs found in the selected output.")
    if any(item.year != YEAR for item in selected):
        raise RuntimeError("A selected film does not have release year 2026.")
    if any(
        item.average_rating is None or item.average_rating <= MIN_RATING_EXCLUSIVE
        for item in selected
    ):
        raise RuntimeError("A selected film does not satisfy rating > 3.4.")
    if any(item.uri in usa_uris for item in selected):
        raise RuntimeError("A USA-filtered film leaked into the final selection.")
    if any(item.uri not in over_10k_uris for item in selected):
        raise RuntimeError("A film without verified >10k watches leaked into the output.")

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
            "Watches": ">10000",
            "WatchThresholdEvidenceURL": urljoin(
                item.uri, f"members/page/{THRESHOLD_PAGE}/"
            ),
            "RatingSource": item.rating_source,
            "MetadataSource": item.metadata_source,
            "CandidateSource": BASE_LIST,
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
            "Watches",
            "WatchThresholdEvidenceURL",
            "RatingSource",
            "MetadataSource",
            "CandidateSource",
            "YearFilterURL",
            "USAExclusionURL",
            "Error",
        ],
        audit_rows,
    )

    summary = {
        "criteria": {
            "year": YEAR,
            "watches_strictly_greater_than": MIN_WATCHES_EXCLUSIVE,
            "watch_threshold_method": (
                f"A non-empty Letterboxd members page {THRESHOLD_PAGE}, validated at "
                f"{MEMBERS_PER_PAGE} members per full page"
            ),
            "rating_strictly_greater_than": MIN_RATING_EXCLUSIVE,
            "exclude_any_film_in_usa_country_filter": True,
        },
        "source_list": BASE_LIST,
        "year_filter_url": year_url,
        "usa_exclusion_url": usa_exclusion_url,
        "counts": {
            "year_candidates": len(year_uris),
            "usa_excluded": len(usa_uris),
            "non_usa_candidates": len(non_usa_candidates),
            "verified_over_10k": len(over_10k_candidates),
            "resolved": len(resolved),
            "selected": len(selected),
        },
        "outputs": {
            "import_csv": str(IMPORT_CSV),
            "audit_csv": str(AUDIT_CSV),
        },
    }
    diagnostics["summary_counts"] = summary["counts"]
    DIAGNOSTICS_JSON.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary["counts"], indent=2), flush=True)
    print(f"Wrote {IMPORT_CSV}", flush=True)
    print(f"Wrote {AUDIT_CSV}", flush=True)


if __name__ == "__main__":
    main()
