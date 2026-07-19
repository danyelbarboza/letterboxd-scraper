from letterboxd_scraper.models import FilmRef
from letterboxd_scraper.parsing import (
    canonicalize_film_uri,
    parse_film_html,
    parse_list_html,
    parse_list_markdown,
)


def test_canonicalize_film_uri_ignores_query_and_unrelated_links() -> None:
    assert (
        canonicalize_film_uri("/film/hachi-a-dogs-tale/?from=search")
        == "https://letterboxd.com/film/hachi-a-dogs-tale/"
    )
    assert canonicalize_film_uri("https://example.com/film/not-letterboxd/") is None


def test_parse_list_html_supports_poster_attributes_and_deduplication() -> None:
    html = """
    <ul>
      <li class="poster-container">
        <div class="film-poster" data-target-link="/film/arrival/"
             data-item-name="Arrival" data-item-year="2016"></div>
      </li>
      <a class="frame" href="/film/arrival/"><img alt="Arrival"></a>
      <div class="react-component" data-target-link="/film/moonlight/"
           data-item-full-display-name="Moonlight (2016)"></div>
    </ul>
    """
    films = parse_list_html(html)

    assert set(films) == {
        "https://letterboxd.com/film/arrival/",
        "https://letterboxd.com/film/moonlight/",
    }
    assert films["https://letterboxd.com/film/arrival/"].title == "Arrival"
    assert films["https://letterboxd.com/film/arrival/"].year == 2016
    assert films["https://letterboxd.com/film/moonlight/"].title == "Moonlight"


def test_parse_list_markdown_extracts_only_poster_rows() -> None:
    markdown = """
    * ![Image 1: Poster for Arrival](https://a.ltrbxd.com/poster.jpg)[Arrival (2016)](https://letterboxd.com/film/arrival/)
    * ![Image 2: Poster for Moonlight](https://a.ltrbxd.com/moon.jpg)[Moonlight (2016)](/film/moonlight/)

    [Navigation link](https://letterboxd.com/film/not-a-list-row/)
    [Footer recommendation](/film/also-not-a-list-row/)
    """
    films = parse_list_markdown(markdown)
    assert set(films) == {
        "https://letterboxd.com/film/arrival/",
        "https://letterboxd.com/film/moonlight/",
    }
    assert films["https://letterboxd.com/film/arrival/"].title == "Arrival"
    assert films["https://letterboxd.com/film/arrival/"].year == 2016


def test_parse_list_markdown_returns_empty_for_page_with_only_incidental_links() -> (
    None
):
    markdown = """
    No films matched this filter.
    [A footer film](https://letterboxd.com/film/false-positive/)
    [Another unrelated film](/film/another-false-positive/)
    """
    assert parse_list_markdown(markdown) == {}


def test_parse_film_html_supports_new_out_of_five_rating_wording() -> None:
    html = """
    <html>
      <head>
        <meta name="twitter:data2" content="3.90 out of 5">
        <meta property="og:title" content="Hachi: A Dog's Tale (2009) • Letterboxd">
      </head>
    </html>
    """
    details = parse_film_html(
        html,
        FilmRef(uri="https://letterboxd.com/film/hachi-a-dogs-tale/"),
    )

    assert details.average_rating == 3.9
    assert details.rating_source == "twitter:data2"
    assert details.title == "Hachi: A Dog's Tale"
    assert details.year == 2009


def test_parse_film_html_supports_legacy_avg_rating_wording() -> None:
    html = """
    <meta name="twitter:data2" content="3.42 avg rating">
    <meta property="og:title" content="Example (2020) • Letterboxd">
    """
    details = parse_film_html(html, FilmRef(uri="https://letterboxd.com/film/example/"))
    assert details.average_rating == 3.42
    assert details.year == 2020


def test_parse_film_html_falls_back_to_json_ld() -> None:
    html = """
    <html><head>
      <meta property="og:title" content="Example • Letterboxd">
      <meta property="og:description" content="A 2018 film.">
      <script type="application/ld+json">
        {"@type":"Movie","aggregateRating":{"ratingValue":"4.12"}}
      </script>
    </head></html>
    """
    details = parse_film_html(html, FilmRef(uri="https://letterboxd.com/film/example/"))
    assert details.average_rating == 4.12
    assert details.rating_source == "json-ld"
    assert details.year == 2018
