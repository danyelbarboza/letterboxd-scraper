from __future__ import annotations

import csv
import json
import os
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
MEMBERS_PAGE_SIZE = 25
MEMBERS_THRESHOLD_PAGE = 400

MIN_CATALOG_PAGES = int(os.getenv("MIN_CATALOG_PAGES", "12"))
MAX_CATALOG_PAGES = int(os.getenv("MAX_CATALOG_PAGES", "20"))
ZERO_PASS_PAGES_TO_STOP = int(os.getenv("ZERO_PASS_PAGES_TO_STOP", "5"))
MAX_USA_CATALOG_PAGES = int(os.getenv("MAX_USA_CATALOG_PAGES", "50"))

CATALOG_URL = f"https://letterboxd.com/films/year/{YEAR}/by/popular/"
USA_CATALOG_URL = f"https://letterboxd.com/films/country/usa/year/{YEAR}/by/popular/"

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


@dataclass(frozen=True, slots=True)
class RatingEvidence:
    uri: str
    average_rating: float | None
    ratings_count: int | None
    url: str
    error: str = ""


@dataclass(frozen=True, slots=True)
class WatchEvidence:
    uri: str
    passes: bool | None
    method: str
    url: str
    member_rows: int | None = None
    error: str = ""


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


def paged_url(base_url: str, page: int) -> str:
    return base_url if page == 1 else urljoin(base_url, f"page/{page}/")


def slug_from_uri(uri: str) -> str:
    segments = [segment for segment in urlsplit(uri).path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "film":
        raise ValueError(f"Unexpected Letterboxd film URI: {uri}")
    return segments[1]


def histogram_url(ref: FilmRef) -> str:
    slug = slug_from_uri(ref.uri)
    return f"https://letterboxd.com/csi/film/{slug}/rating-histogram/"


def members_threshold_url(ref: FilmRef) -> str:
    return urljoin(ref.uri, f"members/page/{MEMBERS_THRESHOLD_PAGE}/")


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
            return RatingEvidence(ref.uri, None, None, url, "Histogram totals not found")
        return RatingEvidence(ref.uri, rating, count, url)
    except Exception as exc:
        return RatingEvidence(ref.uri, None, None, url, repr(exc))


def verify_watch_threshold(
    http: HttpClient,
    ref: FilmRef,
    rating: RatingEvidence,
) -> WatchEvidence:
    # Ratings are a strict subset of watches. More than 10,000 ratings therefore
    # proves more than 10,000 members watched the film.
    if rating.ratings_count is not None and rating.ratings_count > MIN_WATCHES_EXCLUSIVE:
        return WatchEvidence(
            uri=ref.uri,
            passes=True,
            method="ratings_count_strict_lower_bound",
            url=rating.url,
        )

    # Letterboxd publicly exposes 25 members per page and caps this paginator at
    # page 400. A complete page 400 is the public fallback for titles with fewer
    # than 10,001 ratings but at least roughly 10,000 watches.
    url = members_threshold_url(ref)
    try:
        response = http.get(url, allow_404=True)
        if response.status_code == 404:
            return WatchEvidence(ref.uri, False, "members_page_400", url, member_rows=0)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.select_one("table")
        rows = len(table.select("tbody tr")) if table else 0
        return WatchEvidence(
            uri=ref.uri,
            passes=rows == MEMBERS_PAGE_SIZE,
            method="full_members_page_400",
            url=url,
            member_rows=rows,
        )
    except Exception as exc:
        return WatchEvidence(
            ref.uri,
            None,
            "members_page_400",
            url,
            error=repr(exc),
        )


def scan_usa_catalog(http: HttpClient) -> tuple[set[str], list[dict[str, object]]]:
    usa_uris: set[str] = set()
    pages: list[dict[str, object]] = []
    for page in range(1, MAX_USA_CATALOG_PAGES + 1):
        url = paged_url(USA_CATALOG_URL, page)
        markdown = fetch_jina_markdown(http, url, allow_missing=True)
        if markdown is None:
            break
        refs = list(parse_list_markdown(markdown).values())
        refs = [ref for ref in refs if ref.year in (None, YEAR)]
        if not refs:
            break
        before = len(usa_uris)
        usa_uris.update(ref.uri for ref in refs)
        pages.append(
            {
                "page": page,
                "url": url,
                "films": len(refs),
                "new_unique_films": len(usa_uris) - before,
            }
        )
        if len(refs) < 72:
            break
    if not usa_uris:
        raise RuntimeError("The USA-filtered 2026 catalog returned no films.")
    return usa_uris, pages


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    catalog_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=4,
            backoff_base_seconds=1,
            max_backoff_seconds=15,
            concurrency=2,
            min_request_interval_seconds=0.10,
            use_jina_fallback=False,
        )
    )
    rating_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=2,
            backoff_base_seconds=1,
            max_backoff_seconds=8,
            concurrency=3,
            min_request_interval_seconds=0.25,
            use_jina_fallback=False,
        )
    )
    members_http = HttpClient(
        HttpConfig(
            timeout_seconds=60,
            max_attempts=3,
            backoff_base_seconds=1,
            max_backoff_seconds=10,
            concurrency=6,
            min_request_interval_seconds=0.10,
            use_jina_fallback=False,
        )
    )

    refs_by_uri: dict[str, FilmRef] = {}
    ratings_by_uri: dict[str, RatingEvidence] = {}
    watches_by_uri: dict[str, WatchEvidence] = {}
    page_stats: list[dict[str, object]] = []
    unavailable_histograms: list[dict[str, str]] = []
    watch_errors: list[dict[str, str]] = []
    zero_pass_streak = 0

    for page in range(1, MAX_CATALOG_PAGES + 1):
        url = paged_url(CATALOG_URL, page)
        print(f"Catalog page {page}: {url}", flush=True)
        markdown = fetch_jina_markdown(catalog_http, url, allow_missing=True)
        if markdown is None:
            print(f"Catalog ended at page {page - 1}.", flush=True)
            break
        page_refs = list(parse_list_markdown(markdown).values())
        page_refs = [ref for ref in page_refs if ref.year in (None, YEAR)]
        if not page_refs:
            print(f"Catalog page {page} had no 2026 poster rows; stopping.", flush=True)
            break

        page_ratings: dict[str, RatingEvidence] = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(resolve_rating, rating_http, ref): ref for ref in page_refs}
            for future in as_completed(futures):
                ref = futures[future]
                try:
                    evidence = future.result()
                except Exception as exc:
                    evidence = RatingEvidence(ref.uri, None, None, histogram_url(ref), repr(exc))
                page_ratings[ref.uri] = evidence

        for ref in page_refs:
            evidence = page_ratings[ref.uri]
            if evidence.error:
                unavailable_histograms.append(
                    {
                        "Title": ref.title,
                        "LetterboxdURI": ref.uri,
                        "HistogramURL": evidence.url,
                        "Error": evidence.error,
                    }
                )

        rating_candidates = [
            ref
            for ref in page_refs
            if page_ratings[ref.uri].average_rating is not None
            and page_ratings[ref.uri].average_rating > MIN_RATING_EXCLUSIVE
        ]

        page_watches: dict[str, WatchEvidence] = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    verify_watch_threshold,
                    members_http,
                    ref,
                    page_ratings[ref.uri],
                ): ref
                for ref in rating_candidates
            }
            for future in as_completed(futures):
                ref = futures[future]
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
                if evidence.passes is None:
                    watch_errors.append(
                        {
                            "Title": ref.title,
                            "LetterboxdURI": ref.uri,
                            "EvidenceURL": evidence.url,
                            "Error": evidence.error,
                        }
                    )

        page_passes = [
            ref
            for ref in rating_candidates
            if page_watches[ref.uri].passes is True
        ]
        refs_by_uri.update((ref.uri, ref) for ref in page_refs)
        ratings_by_uri.update(page_ratings)
        watches_by_uri.update(page_watches)

        page_stats.append(
            {
                "page": page,
                "url": url,
                "catalog_films": len(page_refs),
                "reachable_histograms": sum(
                    1 for item in page_ratings.values() if item.average_rating is not None
                ),
                "rating_above_3_4": len(rating_candidates),
                "watch_threshold_passes": len(page_passes),
            }
        )
        print(
            f"Page {page}: films={len(page_refs)}, "
            f"histograms={page_stats[-1]['reachable_histograms']}, "
            f"rating>3.4={len(rating_candidates)}, >10k={len(page_passes)}.",
            flush=True,
        )

        zero_pass_streak = zero_pass_streak + 1 if not page_passes else 0
        if page >= MIN_CATALOG_PAGES and zero_pass_streak >= ZERO_PASS_PAGES_TO_STOP:
            print(
                f"Stopping after {zero_pass_streak} consecutive pages without a threshold match.",
                flush=True,
            )
            break

    if len(page_stats) < MIN_CATALOG_PAGES:
        raise RuntimeError(
            f"Only {len(page_stats)} global catalog pages were scanned; "
            f"expected at least {MIN_CATALOG_PAGES}."
        )

    threshold_refs = [
        refs_by_uri[uri]
        for uri, evidence in watches_by_uri.items()
        if evidence.passes is True
    ]
    if not threshold_refs:
        raise RuntimeError("No films passed the rating and watch thresholds.")

    print("Scanning the USA-filtered 2026 catalog for subtraction.", flush=True)
    usa_uris, usa_page_stats = scan_usa_catalog(catalog_http)

    selected = [
        (
            ref,
            ratings_by_uri[ref.uri],
            watches_by_uri[ref.uri],
        )
        for ref in threshold_refs
        if ref.uri not in usa_uris
    ]
    selected.sort(
        key=lambda row: (
            -(row[1].average_rating or 0),
            -(row[1].ratings_count or 0),
            row[0].title.casefold(),
            row[0].uri,
        )
    )
    excluded_us = [ref for ref in threshold_refs if ref.uri in usa_uris]

    if not selected:
        raise RuntimeError("All threshold films appeared in the USA-filtered catalog.")
    if len({ref.uri for ref, _, _ in selected}) != len(selected):
        raise RuntimeError("Duplicate Letterboxd URIs reached the final output.")
    if any((rating.average_rating or 0) <= MIN_RATING_EXCLUSIVE for _, rating, _ in selected):
        raise RuntimeError("A film with rating <= 3.4 reached the final output.")
    if any(watch.passes is not True for _, _, watch in selected):
        raise RuntimeError("A film without watch-threshold evidence reached the final output.")
    if any(ref.uri in usa_uris for ref, _, _ in selected):
        raise RuntimeError("A film from the USA-filtered catalog reached the final output.")

    import_rows = [
        {
            "Title": ref.title,
            "Year": YEAR,
            "LetterboxdURI": ref.uri,
        }
        for ref, _, _ in selected
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
            "AppearsInUSACatalog": False,
            "HistogramURL": rating.url,
            "WatchEvidenceURL": watch.url,
            "USAExclusionCatalog": USA_CATALOG_URL,
        }
        for rank, (ref, rating, watch) in enumerate(selected, 1)
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
            "AppearsInUSACatalog",
            "HistogramURL",
            "WatchEvidenceURL",
            "USAExclusionCatalog",
        ],
        audit_rows,
    )

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "criteria": {
            "release_year": YEAR,
            "rating_strictly_greater_than": MIN_RATING_EXCLUSIVE,
            "watches_requested_strictly_greater_than": MIN_WATCHES_EXCLUSIVE,
            "excluded_if_present_in_usa_country_catalog": True,
        },
        "method": {
            "global_catalog": CATALOG_URL,
            "usa_catalog": USA_CATALOG_URL,
            "primary_watch_evidence": (
                "ratings_count > 10,000; ratings are a strict subset of watches"
            ),
            "fallback_watch_evidence": (
                "a complete public members page 400 with 25 rows"
            ),
        },
        "counts": {
            "global_catalog_pages_scanned": len(page_stats),
            "global_films_scanned": len(refs_by_uri),
            "threshold_films_before_country_exclusion": len(threshold_refs),
            "usa_threshold_films_excluded": len(excluded_us),
            "selected": len(selected),
            "usa_catalog_pages_scanned": len(usa_page_stats),
            "usa_catalog_films_indexed": len(usa_uris),
            "unavailable_histograms": len(unavailable_histograms),
            "watch_check_errors": len(watch_errors),
        },
        "global_catalog_pages": page_stats,
        "usa_catalog_pages": usa_page_stats,
        "usa_exclusions": [
            {"Title": ref.title, "LetterboxdURI": ref.uri}
            for ref in excluded_us
        ],
        "outputs": {
            "import_csv": str(IMPORT_CSV),
            "audit_csv": str(AUDIT_CSV),
        },
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    DIAGNOSTICS_JSON.write_text(
        json.dumps(
            {
                "unavailable_histograms": unavailable_histograms,
                "watch_errors": watch_errors,
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
