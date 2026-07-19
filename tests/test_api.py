from pathlib import Path

import pytest

import letterboxd_scraper.api as api
from letterboxd_scraper.config import AppConfig
from letterboxd_scraper.exceptions import ConfigurationError
from letterboxd_scraper.models import FilmDetails
from letterboxd_scraper.output import OutputPaths
from letterboxd_scraper.pipeline import ScrapeResult


def test_build_list_constructs_a_valid_runtime_config(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, AppConfig] = {}
    sentinel = object()

    class FakePipeline:
        def __init__(self, config: AppConfig) -> None:
            captured["config"] = config

        def run(self) -> object:
            return sentinel

    monkeypatch.setattr(api, "ScrapePipeline", FakePipeline)

    result = api.build_list(
        seed_lists=[" https://letterboxd.com/user/list/example/ "],
        include_lists=["https://letterboxd.com/user/list/include/"],
        min_rating=3.0,
        max_rating=3.5,
        concurrency=4,
        output_directory=tmp_path,
        basename="example",
    )

    assert result is sentinel
    config = captured["config"]
    assert config.query.seed_lists == ("https://letterboxd.com/user/list/example/",)
    assert config.query.include_lists == ("https://letterboxd.com/user/list/include/",)
    assert config.query.min_rating == 3.0
    assert config.query.max_rating == 3.5
    assert config.http.concurrency == 4
    assert config.output.directory == tmp_path
    assert config.output.basename == "example"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seed_lists": []}, "seed_lists"),
        ({"seed_lists": "https://letterboxd.com/user/list/example/"}, "sequence of strings"),
        ({"seed_lists": ["x"], "min_rating": 5.1}, "min_rating"),
        (
            {"seed_lists": ["x"], "min_rating": 4.0, "max_rating": 3.0},
            "min_rating cannot be greater",
        ),
        ({"seed_lists": ["x"], "concurrency": 0}, "concurrency"),
        ({"seed_lists": ["x"], "timeout_seconds": 0}, "timeout_seconds"),
        ({"seed_lists": ["x"], "cache_ttl_hours": -1}, "cache_ttl_hours"),
        ({"seed_lists": ["x"], "basename": "  "}, "basename"),
    ],
)
def test_build_list_rejects_invalid_arguments(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        api.build_list(**kwargs)  # type: ignore[arg-type]


def test_scrape_result_can_be_exported_again(tmp_path: Path) -> None:
    film = FilmDetails(
        uri="https://letterboxd.com/film/example/",
        title="Example",
        year=2020,
        average_rating=4.1,
        rating_source="json-ld",
        metadata_source="film-html",
    )
    result = ScrapeResult(
        candidates={},
        resolved=[film],
        unresolved=[],
        selected=[film],
        list_results=[],
        output_paths=OutputPaths(
            import_csv=tmp_path / "original.csv",
            audit_csv=None,
            unresolved_json=None,
            summary_json=None,
        ),
    )

    import_path = result.to_letterboxd_csv(tmp_path / "nested" / "import.csv")
    audit_path = result.to_audit_csv(tmp_path / "nested" / "audit.csv")

    assert import_path.exists()
    assert audit_path.exists()
    assert "Title,Year,LetterboxdURI" in import_path.read_text(encoding="utf-8-sig")
    assert "AverageRating" in audit_path.read_text(encoding="utf-8-sig")
