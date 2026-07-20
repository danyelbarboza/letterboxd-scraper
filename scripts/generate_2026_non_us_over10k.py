from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from letterboxd_scraper.config import HttpConfig
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.models import FilmRef
from letterboxd_scraper.parsing import parse_list_markdown

YEAR = 2026
MIN_WATCHES_EXCLUSIVE = 10_000
MIN_RATING_EXCLUSIVE = 3.4
CATALOG_URL = f"https://letterboxd.com/films/year/{YEAR}/by/popular/"
MIN_CATALOG_PAGES = 15
MAX_CATALOG_PAGES = 35
ZERO_PASS_PAGES_TO_STOP = 7
MEMBERS_PAGE_SIZE = 25
MEMBERS_THRESHOLD_PAGE = 400

OUTPUT_DIR = Path("output/2026-non-us-over10k-rating-above-3-4")
IMPORT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4.csv"
AUDIT_CSV = OUTPUT_DIR / "letterboxd_2026_non_us_over10k_rating_above_3_4_audit.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
DIAGNOSTICS_JSON = OUTPUT_DIR / "diagnostics.json"

_HISTOGRAM_PATTERN = re.compile(
    r"Weighted average of\s+(?P<rating>[0-5](?:\.\d+)?)\s+based on\s+"
    r"(?P<count>[\d,]+)\s+ratings",
    flags=re.IGNORECASE,
)
_COUNTRY_SECTION_PATTERN = re.compile(
    r"^###\s+Countr(?:y|ies)\s*$\n(?P<body>.*?)(?=^###\s+|\Z)",
    flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
_COUNTRY_LINK_PATTERN = re.compile(
    r"\[\s*(?P<name>[^\]]+?)\s*\]"
    r"\((?:https?://letterboxd\.com)?/films/country/(?P<slug>[^/]+)/\)",
    flags=re.IGNORECASE,
)
_US_COUNTRY_SLUGS = {"usa", "united-states", "united-states-of-america"}


@dataclass(frozen=True, slots=True)
class RatingEvidence:
    uri: str
    average_rating: float | None
    ratings_count: int | None
    histogram_url: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class WatchEvidence:
    uri: str
    passes: bool | None
    method: str
    evidence_url: str
    member_rows: int | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class CountryEvidence:
    uri: str
    countries: tuple[tuple[str, str], ...] | None
    details_url: str
    error: str = ""


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def jina_url(url: str) -> str:
    return f"https://r.jina.ai/{url}"


def fetch_jina_markdown(http: HttpClient, url: str, *, allow_missing: bool = False) -> str | None:
    response = http.get(jina_url(url), allow_404=True)
    text = response.text
    missing_markers = (
        "Warning: Target URL returned error 404",
        "Title: Page Not Found",
        "The page you were looking for doesn’t exist",
        "The page you were looking for doesn't exist",
    )
    if response.status_code == 404 or any(marker in text for marker in missing_markers):
        if allow_missing:
            return None
        raise RuntimeError(f"Required Letterboxd page was unavailable: {url}")
    if "Performing security verification" in text or "Just a moment..." in text:
        raise RuntimeError(f"Jina returned a verification page for {url}")
    if "Warning: Target URL returned error 401" in text:
        raise RuntimeError(f"Jina returned 401 for {url}")
    if not text.strip():
        raise RuntimeError(f"Jina returned an empty page for {url}")
    return text


def catalog_page_url(page: int) -> str:
    return CATALOG_URL if page == 1 else urljoin(CATALOG_URL, f"page/{page}/")


def slug_from_uri(uri: str) -> str:
    segments = [segment for segment in urlsplit(uri).path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "film":
        raise ValueError(f"Unexpected Letterboxd film URI: {uri}")
    return segments[1]


def histogram_url(ref: FilmRef) -> str:
    return f"https://letterboxd.com/csi/film/{slug_from_uri(ref.uri)}/rating-histogram/"


def members_threshold_url(ref: FilmRef) -> str:
    return urljoin(ref.uri, f"members/page/{MEMBERS_THRESHOLD_PAGE}/")


def details_url(ref: FilmRef) -> str:
    return urljoin(ref.uri, "details/")


def parse_histogram(markdown: str) -> tuple[float | None, int | None]:
    match = _HISTOGRAM_PATTERN.search(markdown)
    if not match:
        return None, None
    return float(match.group("rating")), int(match.group("count").replace(",", ""))


def resolve_rating(http: HttpClient, ref: FilmRef) -> RatingEvidence:
    url = histogram_url(ref)
    try:
        markdown = fetch_jina_markdown(http, url, allow_missing=True)
        if markdown is None:
            return RatingEvidence(ref.uri, None, None, url)
        rating, count = parse_histogram(markdown)
        if rating is None or count is None:
            return RatingEvidence(ref.uri, None, None, url, "Histogram totals were not found")
        return RatingEvidence(ref.uri, rating, count, url)
    except Exception as exc:
        return RatingEvidence(ref.uri, None, None, url, repr(exc))


def verify_watch_threshold(http: HttpClient, ref: FilmRef, rating: RatingEvidence) -> WatchEvidence:
    if rating.ratings_count is not None and rating.ratings_count > MIN_WATCHES_EXCLUSIVE:
        return WatchEvidence(
            uri=ref.uri,
            passes=True,
            method="ratings_count_strict_lower_bound",
            evidence_url=rating.histogram_url,
        )

    url = members_threshold_url(ref)
    try:
        response = http.get(url, allow_404=True)
        if response.status_code == 404:
            return WatchEvidence(ref.uri, False, "members_page_400", url, member_rows=0)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.select_one("table")
        rows = len(table.select("tbody tr")) if table else 0
        # A complete page 400 contains members 9,976–10,000. Letterboxd caps
        # this public paginator at page 400, so a full final page is the
        # strongest public threshold check available for low-rating-count films.
        return WatchEvidence(
            uri=ref.uri,
            passes=rows == MEMBERS_PAGE_SIZE,
            method="full_members_page_400",
            evidence_url=url,
            member_rows=rows,
        )
    except Exception as exc:
        return WatchEvidence(ref.uri, None, "members_page_400", url, error=repr(exc))


def extract_countries(markdown: str) -> tuple[tuple[str, str], ...]:
    section = _COUNTRY_SECTION_PATTERN.search(markdown)
    if not section:
        return ()
    countries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _COUNTRY_LINK_PATTERN.finditer(section.group("body")):
        name = match.group("name").strip()
        slug = match.group("slug").strip().casefold()
        if slug in seen:
            continue
        seen.add(slug)
        countries.append((name, slug))
    return tuple(countries)


def resolve_countries(http: HttpClient, ref: FilmRef) -> CountryEvidence:
    url = details_url(ref)
    try:
        markdown = fetch_jina_markdown(http, url)
        assert markdown is not None
        countries = extract_countries(markdown)
        if not countries:
            return CountryEvidence(ref.uri, None, url, "Country section was missing or empty")
        return CountryEvidence(ref.uri, countries, url)
    except Exception as exc:
        return CountryEvidence(ref.uri, None, url, repr(exc))


def is_us_production(countries: tuple[tuple[str, str], ...]) -> bool:
    for name, slug in countries:
        normalized_name = name.casefold().replace(".", "").strip()
        if slug in _US_COUNTRY_SLUGS:
            return True
        if normalized_name in {"usa", "us", "united states", "united states of america"}:
            return True
    return False


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=5,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=2,
            min_request_interval_seconds=0.08,
            use_jina_fallback=False,
        )
    )
    rating_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=5,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=14,
            min_request_interval_seconds=0.05,
            use_jina_fallback=False,
        )
    )
    members_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=4,
            backoff_base_seconds=1,
            max_backoff_seconds=15,
            concurrency=8,
            min_request_interval_seconds=0.08,
            use_jina_fallback=False,
        )
    )
    country_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=5,
            backoff_base_seconds=1,
            max_backoff_seconds=20,
            concurrency=10,
            min_request_interval_seconds=0.06,
            use_jina_fallback=False,
        )
    )

    refs_by_uri: dict[str, FilmRef] = {}
    ratings_by_uri: dict[str, RatingEvidence] = {}
    watches_by_uri: dict[str, WatchEvidence] = {}
    page_stats: list[dict[str, object]] = []
    zero_pass_streak = 0
    scan_errors: list[dict[str, str]] = []

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = catalog_page_url(page)
        print(f"Catalog page {page}: {url}", flush=True)
        markdown = fetch_jina_markdown(catalog_http, url, allow_missing=True)
        if markdown is None:
            print(f"Catalog ended at page {page - 1}.", flush=True)
            break
        page_refs = list(parse_list_markdown(markdown).values())
        page_refs = [ref for ref in page_refs if ref.year in (None, YEAR)]
        if not page_refs:
            print(f"Catalog page {page} contained no 2026 poster rows; stopping.", flush=True)
            break

        page_ratings: dict[str, RatingEvidence] = {}
        with ThreadPoolExecutor(max_workers=14) as executor:
            future_map = {executor.submit(resolve_rating, rating_http, ref): ref for ref in page_refs}
            for future in as_completed(future_map):
                ref = future_map[future]
                try:
                    evidence = future.result()
                except Exception as exc:
                    evidence = RatingEvidence(ref.uri, None, None, histogram_url(ref), repr(exc))
                page_ratings[ref.uri] = evidence

        rating_errors = [item for item in page_ratings.values() if item.error]
        if rating_errors:
            scan_errors.extend(
                {
                    "Title": next((ref.title for ref in page_refs if ref.uri == item.uri), ""),
                    "LetterboxdURI": item.uri,
                    "Stage": "rating_histogram",
                    "Error": item.error,
                }
                for item in rating_errors
            )
            raise RuntimeError(
                f"Could not parse {len(rating_errors)} rating histograms on page {page}; "
                f"first error: {rating_errors[0].error}"
            )

        rating_candidates = [
            ref
            for ref in page_refs
            if page_ratings[ref.uri].average_rating is not None
            and page_ratings[ref.uri].average_rating > MIN_RATING_EXCLUSIVE
        ]

        page_watches: dict[str, WatchEvidence] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_map = {
                executor.submit(
                    verify_watch_threshold,
                    members_http,
                    ref,
                    page_ratings[ref.uri],
                ): ref
                for ref in rating_candidates
            }
            for future in as_completed(future_map):
                ref = future_map[future]
                try:
                    evidence = future.result()
                except Exception as exc:
                    evidence = WatchEvidence(
                        ref.uri,
                        None,
                        "threshold_check",
                        members_threshold_url(ref),
                        error=repr(exc),
                    )
                page_watches[ref.uri] = evidence

        watch_errors = [item for item in page_watches.values() if item.passes is None]
        if watch_errors:
            scan_errors.extend(
                {
                    "Title": next((ref.title for ref in page_refs if ref.uri == item.uri), ""),
                    "LetterboxdURI": item.uri,
                    "Stage": "watch_threshold",
                    "Error": item.error,
                }
                for item in watch_errors
            )
            raise RuntimeError(
                f"Could not verify {len(watch_errors)} watch thresholds on page {page}; "
                f"first error: {watch_errors[0].error}"
            )

        page_passes = [ref for ref in rating_candidates if page_watches[ref.uri].passes is True]
        for ref in page_refs:
            refs_by_uri[ref.uri] = ref
            ratings_by_uri[ref.uri] = page_ratings[ref.uri]
        for ref in rating_candidates:
            watches_by_uri[ref.uri] = page_watches[ref.uri]

        page_stats.append(
            {
                "page": page,
                "url": url,
                "catalog_films": len(page_refs),
                "rating_above_3_4": len(rating_candidates),
                "watch_threshold_passes": len(page_passes),
            }
        )
        print(
            f"Page {page}: films={len(page_refs)}, rating>3.4={len(rating_candidates)}, "
            f">10k evidence={len(page_passes)}.",
            flush=True,
        )

        zero_pass_streak = zero_pass_streak + 1 if not page_passes else 0
        if page >= MIN_CATALOG_PAGES and zero_pass_streak >= ZERO_PASS_PAGES_TO_STOP:
            print(
                f"Stopping after {zero_pass_streak} consecutive popularity pages without a match.",
                flush=True,
            )
            break

    if len(page_stats) < MIN_CATALOG_PAGES:
        raise RuntimeError(
            f"Only {len(page_stats)} catalog pages were scanned; expected at least {MIN_CATALOG_PAGES}."
        )

    threshold_refs = [
        refs_by_uri[uri]
        for uri, watch in watches_by_uri.items()
        if watch.passes is True
    ]
    if not threshold_refs:
        raise RuntimeError("No 2026 films passed both the rating and watch thresholds.")

    countries_by_uri: dict[str, CountryEvidence] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_map = {executor.submit(resolve_countries, country_http, ref): ref for ref in threshold_refs}
        for future in as_completed(future_map):
            ref = future_map[future]
            try:
                evidence = future.result()
            except Exception as exc:
                evidence = CountryEvidence(ref.uri, None, details_url(ref), repr(exc))
            countries_by_uri[ref.uri] = evidence

    country_errors = [item for item in countries_by_uri.values() if item.countries is None]
    if country_errors:
        raise RuntimeError(
            f"Could not resolve countries for {len(country_errors)} threshold films; "
            f"first error: {country_errors[0].error}"
        )

    selected: list[tuple[FilmRef, RatingEvidence, WatchEvidence, CountryEvidence]] = []
    excluded_us: list[dict[str, object]] = []
    for ref in threshold_refs:
        rating = ratings_by_uri[ref.uri]
        watch = watches_by_uri[ref.uri]
        country = countries_by_uri[ref.uri]
        assert country.countries is not None
        if is_us_production(country.countries):
            excluded_us.append(
                {
                    "Title": ref.title,
                    "LetterboxdURI": ref.uri,
                    "Countries": [name for name, _ in country.countries],
                }
            )
            continue
        selected.append((ref, rating, watch, country))

    selected.sort(
        key=lambda row: (
            -(row[1].average_rating or 0),
            -(row[1].ratings_count or 0),
            row[0].title.casefold(),
            row[0].uri,
        )
    )

    if not selected:
        raise RuntimeError("Every threshold film was a USA production; final selection is empty.")
    if len({ref.uri for ref, _, _, _ in selected}) != len(selected):
        raise RuntimeError("Duplicate Letterboxd URIs were found in the final output.")
    if any(ref.year not in (None, YEAR) for ref, _, _, _ in selected):
        raise RuntimeError("A film outside 2026 reached the final output.")
    if any((rating.average_rating or 0) <= MIN_RATING_EXCLUSIVE for _, rating, _, _ in selected):
        raise RuntimeError("A film with rating <= 3.4 reached the final output.")
    if any(watch.passes is not True for _, _, watch, _ in selected):
        raise RuntimeError("A film without watch-threshold evidence reached the final output.")
    if any(
        is_us_production(country.countries or ())
        for _, _, _, country in selected
    ):
        raise RuntimeError("A USA production reached the final output.")

    import_rows = [
        {
            "Title": ref.title,
            "Year": YEAR,
            "LetterboxdURI": ref.uri,
        }
        for ref, _, _, _ in selected
    ]
    audit_rows = [
        {
            "Rank": rank,
            "Title": ref.title,
            "Year": YEAR,
            "LetterboxdURI": ref.uri,
            "AverageRating": f"{rating.average_rating:.2f}",
            "RatingsCount": rating.ratings_count,
            "WatchThresholdMethod": watch.method,
            "MemberRowsOnPage400": watch.member_rows if watch.member_rows is not None else "",
            "Countries": " | ".join(name for name, _ in (country.countries or ())),
            "HistogramURL": rating.histogram_url,
            "WatchEvidenceURL": watch.evidence_url,
            "CountryEvidenceURL": country.details_url,
        }
        for rank, (ref, rating, watch, country) in enumerate(selected, 1)
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
            "RatingsCount",
            "WatchThresholdMethod",
            "MemberRowsOnPage400",
            "Countries",
            "HistogramURL",
            "WatchEvidenceURL",
            "CountryEvidenceURL",
        ],
        audit_rows,
    )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "release_year": YEAR,
            "rating_strictly_greater_than": MIN_RATING_EXCLUSIVE,
            "watches_requested_strictly_greater_than": MIN_WATCHES_EXCLUSIVE,
            "exclude_any_production_listing_usa": True,
        },
        "method": {
            "catalog": CATALOG_URL,
            "watch_threshold_primary": (
                "ratings_count > 10,000; every rating is necessarily attached to a member "
                "who watched the film, so this is a strict lower bound on watches"
            ),
            "watch_threshold_fallback": (
                "a full public members page 400 (25 rows, covering members 9,976–10,000); "
                "Letterboxd caps this paginator publicly at page 400"
            ),
        },
        "counts": {
            "catalog_pages_scanned": len(page_stats),
            "unique_catalog_films_scanned": len(refs_by_uri),
            "rating_and_watch_threshold_films": len(threshold_refs),
            "usa_productions_excluded": len(excluded_us),
            "selected": len(selected),
        },
        "catalog_pages": page_stats,
        "usa_exclusions": excluded_us,
        "outputs": {
            "import_csv": str(IMPORT_CSV),
            "audit_csv": str(AUDIT_CSV),
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    DIAGNOSTICS_JSON.write_text(
        json.dumps(
            {
                "scan_errors": scan_errors,
                "page_stats": page_stats,
                "country_errors": [
                    {
                        "LetterboxdURI": item.uri,
                        "DetailsURL": item.details_url,
                        "Error": item.error,
                    }
                    for item in country_errors
                ],
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
