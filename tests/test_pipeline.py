from letterboxd_scraper.config import AppConfig, QueryConfig
from letterboxd_scraper.models import FilmDetails, FilmRef
from letterboxd_scraper.pipeline import apply_list_algebra, filter_by_rating


def ref(slug: str, title: str = "") -> FilmRef:
    return FilmRef(uri=f"https://letterboxd.com/film/{slug}/", title=title)


def details(slug: str, rating: float) -> FilmDetails:
    return FilmDetails(
        uri=f"https://letterboxd.com/film/{slug}/",
        title=slug,
        year=2020,
        average_rating=rating,
    )


def test_apply_list_algebra_unions_intersects_and_subtracts() -> None:
    all_films = {item.uri: item for item in [ref("a"), ref("b"), ref("c"), ref("d")]}
    over_10k = {item.uri: item for item in [ref("b", "B"), ref("c"), ref("d")]}
    over_100k = {ref("d").uri: ref("d")}

    result = apply_list_algebra(
        seed=[all_films],
        include=[over_10k],
        exclude=[over_100k],
    )

    assert set(result) == {ref("b").uri, ref("c").uri}
    assert result[ref("b").uri].title == "B"


def test_filter_by_rating_honors_inclusive_boundaries() -> None:
    config = AppConfig(
        query=QueryConfig(
            seed_lists=("https://letterboxd.com/user/list/example/",),
            min_rating=3.0,
            max_rating=3.5,
            min_rating_inclusive=True,
            max_rating_inclusive=True,
        )
    )
    selected = filter_by_rating(
        [details("below", 2.99), details("min", 3.0), details("max", 3.5), details("above", 3.51)],
        config,
    )
    assert [film.uri for film in selected] == [details("min", 3.0).uri, details("max", 3.5).uri]


def test_filter_by_rating_honors_exclusive_minimum() -> None:
    config = AppConfig(
        query=QueryConfig(
            seed_lists=("https://letterboxd.com/user/list/example/",),
            min_rating=3.5,
            min_rating_inclusive=False,
        )
    )
    selected = filter_by_rating([details("equal", 3.5), details("higher", 3.51)], config)
    assert [film.uri for film in selected] == [details("higher", 3.51).uri]
