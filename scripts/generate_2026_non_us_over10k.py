from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig, HttpConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.models import FilmDetails, FilmRef
from letterboxd_scraper.parsing import parse_list_markdown

YEAR = 2026
MIN_WATCHES_EXCLUSIVE = 10_000
MIN_RATING_EXCLUSIVE = 3.4
MEMBERS_PER_PAGE = 25
WATCH_THRESHOLD_PAGE = MIN_WATCHES_EXCLUSIVE // MEMBERS_PER_PAGE + 1
CATALOG_URL = f"https://letterboxd.com/films/year/{YEAR}/by/popular/"
MIN_CATALOG_PAGES = 12
MAX_CATALOG_PAGES = 30
ZERO_PASS_PAGES_TO_STOP = 4

OUTPUT_DIR = Path("output/2026-non-us-over10k-rating-above-3-4")
IMPORT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4.csv"
AUDIT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4_audit.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
DIAGNOSTICS_JSON = OUTPUT_DIR / "diagnostics.json"

PAGE_SIZE_VALIDATION_FILM = "https://letterboxd.com/film/project-hail-mary/"
_ACTIVITY_PATTERN = re.compile(r"^Activity for film\b", flags=re.MULTILINE)
_COUNTRY_SECTION_PATTERN = re.compile(
    r"^###\s+Countr(?:y|ies)\s*$\n(?P<body>.*?)(?=^###\s+|\Z)",
    flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_COUNTRY_LINK_PATTERN = re.compile(
    r"\[\s*(?P<name>[^\]]+?)\s*\]"
    r"\((?:https?://letterboxd\.com)?/films/country/(?P<slug>[^/]+)/\)",
    flags=re.IGNORECASE,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def jina_url(url: str) -> str:
    return f"https://r.jina.ai/{url}"


def fetch_jina_markdown(
    http: HttpClient,
    url: str,
    *,
    allow_missing: bool = False,
) -> str | None:
    response = http.get(jina_url(url), allow_404=True)
    if response.status_code == 404:
        return None if allow_missing else _raise_missing(url)

    text = response.text
    missing_markers = (
        "Warning: Target URL returned error 404",
        "Title: Page Not Found",
        "The page you were looking for doesn’t exist",
        "The page you were looking for doesn't exist",
    )
    if any(marker in text for marker in missing_markers):
        return None if allow_missing else _raise_missing(url)
    if "Performing security verification" in text or "Just a moment..." in text:
        raise RuntimeError(f"Jina returned a verification page for {url}")
    if not text.strip():
        raise RuntimeError(f"Jina returned an empty page for {url}")
    return text


def _raise_missing(url: str) -> None:
    raise RuntimeError(f"Required Letterboxd page was unavailable: {url}")


def catalog_page_url(page: int) -> str:
    return CATALOG_URL if page == 1 else urljoin(CATALOG_URL, f"page/{page}/")


def watch_threshold_url(ref: FilmRef | FilmDetails) -> str:
    return urljoin(ref.uri, f"members/page/{WATCH_THRESHOLD_PAGE}/")


def count_member_rows(markdown: str) -> int:
    return len(_ACTIVITY_PATTERN.findall(markdown))


def validate_members_page_size(http: HttpClient) -> None:
    url = urljoin(PAGE_SIZE_VALIDATION_FILM, "members/")
    markdown = fetch_jina_markdown(http, url)
    assert markdown is not None
    count = count_member_rows(markdown)
    if count != MEMBERS_PER_PAGE:
        raise RuntimeError(
            f"Expected {MEMBERS_PER_PAGE} rows on a full members page, "
            f"but parsed {count} from {url}."
        )


def check_over_10k(
    http: HttpClient,
    ref: FilmRef,
) -> tuple[str, bool | None, str]:
    url = watch_threshold_url(ref)
    try:
        markdown = fetch_jina_markdown(http, url, allow_missing=True)
    except Exception as exc:
        return ref.uri, None, repr(exc)
    if markdown is None:
        return ref.uri, False, ""
    return ref.uri, count_member_rows(markdown) > 0, ""


def extract_countries(markdown: str) -> list[dict[str, str]]:
    section = _COUNTRY_SECTION_PATTERN.search(markdown)
    if not section:
        return []

    countries: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _COUNTRY_LINK_PATTERN.finditer(section.group("body")):
        slug = match.group("slug").strip().casefold()
        if slug in seen:
            continue
        seen.add(slug)
        countries.append(
            {
                "name": match.group("name").strip(),
                "slug": slug,
            }
        )
    return countries


def resolve_countries(
    http: HttpClient,
    details: FilmDetails,
) -> tuple[str, list[dict[str, str]] | None, str]:
    details_url = urljoin(details.uri, "details/")
    try:
        markdown = fetch_jina_markdown(http, details_url)
        assert markdown is not None
        countries = extract_countries(markdown)
        if not countries:
            return details.uri, None, "Country section was missing or empty"
        return details.uri, countries, ""
    except Exception as exc:
        return details.uri, None, repr(exc)


def scan_catalog(
    catalog_http: HttpClient,
    threshold_http: HttpClient,
) -> tuple[dict[str, FilmRef], list[dict[str, object]]]:
    verified: dict[str, FilmRef] = {}
    page_stats: list[dict[str, object]] = []
    zero_pass_streak = 0

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = catalog_page_url(page)
        print(f"Catalog page {page}: {url}", flush=True)
        markdown = fetch_jina_markdown(catalog_http, url, allow_missing=True)
        if markdown is None:
            print(f"Catalog ended before page {page}.", flush=True)
            break

        page_films = list(parse_list_markdown(markdown).values())
        if not page_films:
            print(f"Catalog page {page} contained no poster rows; stopping.", flush=True)
            break

        page_errors: list[dict[str, str]] = []
        page_passes: dict[str, FilmRef] = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(check_over_10k, threshold_http, ref): ref
                for ref in page_films
            }
            for future in as_completed(futures):
                ref = futures[future]
                try:
                    uri, passes, error = future.result()
                except Exception as exc:
                    uri, passes, error = ref.uri, None, repr(exc)

                if passes is True:
                    page_passes[uri] = ref
                elif passes is None:
                    page_errors.append(
                        {
                            "Title": ref.title,
                            "LetterboxdURI": ref.uri,
                            "ThresholdURL": watch_threshold_url(ref),
                            "Error": error,
                        }
                    )

        if page_errors:
            raise RuntimeError(
                f"Could not verify the watch threshold for {len(page_errors)} titles "
                f"on catalog page {page}: {page_errors[:3]}"
            )

        verified.update(page_passes)
        page_stats.append(
            {
                "page": page,
                "url": url,
                "catalog_films": len(page_films),
                "verified_over_10k": len(page_passes),
            }
        )
        print(
            f"Page {page}: {len(page_films)} titles, "
            f"{len(page_passes)} verified above 10,000 watches; "
            f"running total={len(verified)}.",
            flush=True,
        )

        zero_pass_streak = zero_pass_streak + 1 if not page_passes else 0
        if page >= MIN_CATALOG_PAGES and zero_pass_streak >= ZERO_PASS_PAGES_TO_STOP:
            print(
                f"Stopping after {zero_pass_streak} consecutive popularity pages "
                "without a title above 10,000 watches.",
                flush=True,
            )
            break

    if not page_stats:
        raise RuntimeError("No 2026 catalog pages could be parsed.")
    if len(page_stats) < MIN_CATALOG_PAGES and page_stats[-1]["catalog_films"] == 72:
        raise RuntimeError(
            f"Only {len(page_stats)} full catalog pages were scanned; refusing a partial export."
        )
    if not verified:
        raise RuntimeError("No 2026 titles were verified above 10,000 watches.")

    return verified, page_stats


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=5,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=2,
            min_request_interval_seconds=0.10,
            use_jina_fallback=False,
        )
    )
    threshold_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=5,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=10,
            min_request_interval_seconds=0.08,
            use_jina_fallback=False,
        )
    )
    metadata_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=6,
            backoff_base_seconds=1,
            max_backoff_seconds=25,
            concurrency=8,
            min_request_interval_seconds=0.10,
            use_jina_fallback=True,
        )
    )
    country_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=6,
            backoff_base_seconds=1,
            max_backoff_seconds=25,
            concurrency=8,
            min_request_interval_seconds=0.10,
            use_jina_fallback=False,
        )
    )

    print("Validating the Letterboxd members-page pagination assumption.", flush=True)
    validate_members_page_size(threshold_http)

    over_10k_refs, page_stats = scan_catalog(catalog_http, threshold_http)

    print(f"Resolving ratings for {len(over_10k_refs)} verified titles.", flush=True)
    resolver = FilmResolver(
        metadata_http,
        FilmCache(
            CacheConfig(
                enabled=True,
                directory=Path(".cache/letterboxd-2026-live-catalog"),
                ttl_hours=24,
            )
        ),
        concurrency=8,
    )
    resolved, unresolved = resolver.resolve_many(over_10k_refs)

    rating_candidates = [
        item
        for item in resolved
        if item.year == YEAR
        and item.average_rating is not None
        and item.average_rating > MIN_RATING_EXCLUSIVE
    ]
    print(
        f"Ratings resolved={len(resolved)}, unavailable={len(unresolved)}, "
        f"above 3.4={len(rating_candidates)}.",
        flush=True,
    )

    countries_by_uri: dict[str, list[dict[str, str]]] = {}
    country_errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(resolve_countries, country_http, item): item
            for item in rating_candidates
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                uri, countries, error = future.result()
            except Exception as exc:
                uri, countries, error = item.uri, None, repr(exc)
            if countries is None:
                country_errors.append(
                    {
                        "Title": item.title,
                        "LetterboxdURI": item.uri,
                        "Error": error,
                    }
                )
            else:
                countries_by_uri[uri] = countries

    if country_errors:
        DIAGNOSTICS_JSON.write_text(
            json.dumps(
                {
                    "country_errors": country_errors,
                    "unresolved_ratings": [
                        {
                            "Title": item.title,
                            "Year": item.year,
                            "LetterboxdURI": item.uri,
                            "Error": item.error,
                        }
                        for item in unresolved
                    ],
                    "catalog_pages": page_stats,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        raise RuntimeError(
            f"Country metadata could not be verified for {len(country_errors)} titles."
        )

    selected: list[FilmDetails] = []
    excluded_usa: list[FilmDetails] = []
    for item in rating_candidates:
        countries = countries_by_uri[item.uri]
        country_slugs = {country["slug"] for country in countries}
        if "usa" in country_slugs:
            excluded_usa.append(item)
        else:
            selected.append(item)

    selected.sort(
        key=lambda item: (
            -(item.average_rating or 0.0),
            item.title.casefold(),
            item.uri,
        )
    )

    if not selected:
        raise RuntimeError("No titles matched every requested criterion.")
    if len({item.uri for item in selected}) != len(selected):
        raise RuntimeError("Duplicate canonical Letterboxd URIs found in the output.")
    if any(item.year != YEAR for item in selected):
        raise RuntimeError("A selected title does not have release year 2026.")
    if any(
        item.average_rating is None or item.average_rating <= MIN_RATING_EXCLUSIVE
        for item in selected
    ):
        raise RuntimeError("A selected title does not satisfy rating > 3.4.")
    if any(
        "usa" in {country["slug"] for country in countries_by_uri[item.uri]}
        for item in selected
    ):
        raise RuntimeError("A USA production or co-production leaked into the output.")
    if any(item.uri not in over_10k_refs for item in selected):
        raise RuntimeError("A title without verified >10k watches leaked into the output.")

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
            "Countries": "; ".join(
                country["name"] for country in countries_by_uri[item.uri]
            ),
            "CountrySlugs": "; ".join(
                country["slug"] for country in countries_by_uri[item.uri]
            ),
            "Watches": ">10000",
            "WatchThresholdEvidenceURL": watch_threshold_url(item),
            "RatingSource": item.rating_source,
            "MetadataSource": item.metadata_source,
            "CatalogSource": CATALOG_URL,
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
            "Countries",
            "CountrySlugs",
            "Watches",
            "WatchThresholdEvidenceURL",
            "RatingSource",
            "MetadataSource",
            "CatalogSource",
        ],
        audit_rows,
    )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "release_year": YEAR,
            "watches_strictly_greater_than": MIN_WATCHES_EXCLUSIVE,
            "rating_strictly_greater_than": MIN_RATING_EXCLUSIVE,
            "country_rule": "Exclude any title whose Letterboxd country metadata includes USA",
        },
        "method": {
            "catalog": CATALOG_URL,
            "catalog_order": "Letterboxd all-time film popularity",
            "minimum_catalog_pages": MIN_CATALOG_PAGES,
            "stop_rule": (
                f"After page {MIN_CATALOG_PAGES}, stop following "
                f"{ZERO_PASS_PAGES_TO_STOP} consecutive pages with no title above 10k watches"
            ),
            "watch_threshold": (
                f"Page {WATCH_THRESHOLD_PAGE} of each film's members list must contain "
                "at least one member; full pages were validated at 25 members"
            ),
        },
        "counts": {
            "catalog_pages_scanned": len(page_stats),
            "catalog_titles_scanned": sum(
                int(page["catalog_films"]) for page in page_stats
            ),
            "verified_over_10k": len(over_10k_refs),
            "rating_resolved": len(resolved),
            "rating_unavailable": len(unresolved),
            "rating_above_3_4": len(rating_candidates),
            "excluded_for_usa_country": len(excluded_usa),
            "selected": len(selected),
        },
        "catalog_pages": page_stats,
        "unresolved_ratings": [
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
    SUMMARY_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    DIAGNOSTICS_JSON.write_text(
        json.dumps(
            {
                "catalog_pages": page_stats,
                "excluded_usa": [
                    {
                        "Title": item.title,
                        "Year": item.year,
                        "LetterboxdURI": item.uri,
                        "AverageRating": item.average_rating,
                        "Countries": countries_by_uri[item.uri],
                    }
                    for item in excluded_usa
                ],
                "unresolved_ratings": summary["unresolved_ratings"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(summary["counts"], indent=2), flush=True)
    print(f"Wrote {IMPORT_CSV}", flush=True)
    print(f"Wrote {AUDIT_CSV}", flush=True)


if __name__ == "__main__":
    main()
