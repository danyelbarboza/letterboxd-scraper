"""Letterboxd list pagination with direct and Jina Reader parsing strategies."""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit, urlunsplit

from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.models import FilmRef, ListScrapeResult
from letterboxd_scraper.parsing import parse_list_html, parse_list_markdown


class ListScraper:
    """Scrape one public Letterboxd list into canonical film references."""

    def __init__(self, http: HttpClient, *, filters: tuple[str, ...], max_pages: int) -> None:
        self._http = http
        self._filters = filters
        self._max_pages = max_pages

    def scrape(self, list_url: str) -> ListScrapeResult:
        films: dict[str, FilmRef] = {}
        source_counts: Counter[str] = Counter()
        pages_read = 0

        for page in range(1, self._max_pages + 1):
            page_films, source, is_end = self._fetch_page(list_url, page)
            source_counts[source] += 1
            if is_end:
                break

            new_count = 0
            for uri, film in page_films.items():
                if uri in films:
                    films[uri] = films[uri].merge(film)
                else:
                    films[uri] = film
                    new_count += 1

            pages_read = page
            if not page_films or new_count == 0:
                break

        return ListScrapeResult(
            list_url=normalize_list_url(list_url),
            films=films,
            pages_read=pages_read,
            source_counts=dict(source_counts),
        )

    def _fetch_page(self, list_url: str, page: int) -> tuple[dict[str, FilmRef], str, bool]:
        page_urls = build_list_page_urls(list_url, page, self._filters)

        for url in page_urls:
            try:
                response = self._http.get(url, allow_404=True)
            except Exception:
                continue
            if response.status_code == 404:
                continue
            films = parse_list_html(response.text)
            if films:
                return films, "direct-html", False

        # Direct list pages commonly return HTTP 403 on hosted runners. Jina
        # Reader is a pragmatic read-only fallback for public pages.
        for url in page_urls:
            try:
                response = self._http.get_jina(url)
            except Exception:
                continue
            films = parse_list_markdown(response.text)
            if films:
                return films, "jina-markdown", False

        # Letterboxd often returns 404 for the first page beyond the end of a
        # list rather than returning an empty successful page.
        return {}, "end-or-unavailable", True


def normalize_list_url(list_url: str) -> str:
    """Remove query strings and normalize a list URL to one trailing slash."""
    parsed = urlsplit(list_url.strip())
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((parsed.scheme or "https", parsed.netloc, path, "", ""))


def _detail_list_url(list_url: str) -> str:
    """Insert Letterboxd's ``detail`` view segment before list filters."""
    parsed = urlsplit(normalize_list_url(list_url))
    segments = [segment for segment in parsed.path.split("/") if segment]
    try:
        list_index = segments.index("list")
    except ValueError:
        return normalize_list_url(list_url)

    insertion_index = list_index + 2
    if insertion_index <= len(segments) and (
        insertion_index == len(segments) or segments[insertion_index] != "detail"
    ):
        segments.insert(insertion_index, "detail")
    path = "/" + "/".join(segments) + "/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def build_list_page_urls(
    list_url: str,
    page: int,
    filters: tuple[str, ...],
) -> tuple[str, ...]:
    """Build detail and grid variants without moving existing path filters.

    Letterboxd places ``detail`` immediately after the list slug and before
    filters such as ``country`` and ``language``. The first page is addressed
    without an explicit ``page/1`` segment.
    """
    grid_base = normalize_list_url(list_url).rstrip("/")
    detail_base = _detail_list_url(list_url).rstrip("/")
    suffix = "" if page == 1 else f"/page/{page}"
    query = f"?filters={'+'.join(filters)}" if filters else ""
    return (
        f"{detail_base}{suffix}/{query}",
        f"{grid_base}{suffix}/{query}",
    )
