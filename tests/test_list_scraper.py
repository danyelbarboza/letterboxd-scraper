from dataclasses import dataclass

from letterboxd_scraper.http import HttpResponse
from letterboxd_scraper.list_scraper import (
    ListScraper,
    build_list_page_urls,
    normalize_list_url,
)


@dataclass
class FakeHttp:
    calls: int = 0

    def get(self, url: str, *, allow_404: bool = False) -> HttpResponse:
        self.calls += 1
        if url.endswith("/detail/?filters=hide-tv"):
            return HttpResponse(
                url=url,
                status_code=200,
                text=(
                    '<div class="film-poster" data-target-link="/film/example/" '
                    'data-item-name="Example" data-item-year="2020"></div>'
                ),
                source="direct",
            )
        return HttpResponse(url=url, status_code=404, text="", source="direct")

    def get_jina(self, url: str) -> HttpResponse:
        raise AssertionError(f"Jina should not be needed: {url}")


def test_list_scraper_stops_when_next_page_returns_404() -> None:
    http = FakeHttp()
    scraper = ListScraper(http, filters=("hide-tv",), max_pages=10)  # type: ignore[arg-type]

    result = scraper.scrape("https://letterboxd.com/user/list/example/?foo=bar")

    assert len(result.films) == 1
    assert result.pages_read == 1
    assert result.source_counts == {"direct-html": 1, "end-or-unavailable": 1}


def test_list_url_helpers_build_plain_list_pages() -> None:
    normalized = normalize_list_url("https://letterboxd.com/user/list/example/?foo=bar")
    assert normalized == "https://letterboxd.com/user/list/example/"
    assert build_list_page_urls(normalized, 1, ()) == (
        "https://letterboxd.com/user/list/example/detail/",
        "https://letterboxd.com/user/list/example/",
    )
    assert build_list_page_urls(normalized, 2, ("hide-tv", "hide-shorts")) == (
        "https://letterboxd.com/user/list/example/detail/page/2/?filters=hide-tv+hide-shorts",
        "https://letterboxd.com/user/list/example/page/2/?filters=hide-tv+hide-shorts",
    )


def test_list_url_helpers_keep_country_and_language_filters_after_detail() -> None:
    filtered = "https://letterboxd.com/user/list/example/country/brazil/language/portuguese/"
    assert build_list_page_urls(filtered, 1, ()) == (
        "https://letterboxd.com/user/list/example/detail/country/brazil/language/portuguese/",
        "https://letterboxd.com/user/list/example/country/brazil/language/portuguese/",
    )
    assert build_list_page_urls(filtered, 2, ()) == (
        "https://letterboxd.com/user/list/example/detail/country/brazil/language/portuguese/page/2/",
        "https://letterboxd.com/user/list/example/country/brazil/language/portuguese/page/2/",
    )
