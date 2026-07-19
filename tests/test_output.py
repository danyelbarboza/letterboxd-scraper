import csv
import json
from pathlib import Path

from letterboxd_scraper.config import AppConfig, OutputConfig, QueryConfig
from letterboxd_scraper.models import FilmDetails
from letterboxd_scraper.output import write_outputs


def test_write_outputs_creates_letterboxd_import_and_summary(tmp_path: Path) -> None:
    config = AppConfig(
        query=QueryConfig(seed_lists=("https://letterboxd.com/user/list/example/",)),
        output=OutputConfig(directory=tmp_path, basename="sample"),
    )
    films = [
        FilmDetails(
            uri="https://letterboxd.com/film/example/",
            title="Example",
            year=2020,
            average_rating=3.5,
            rating_source="twitter:data2",
            metadata_source="film-html",
        )
    ]

    paths = write_outputs(films, [], [], config)

    with paths.import_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "Title": "Example",
            "Year": "2020",
            "LetterboxdURI": "https://letterboxd.com/film/example/",
        }
    ]

    assert paths.summary_json is not None
    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["selected_rows"] == 1
    assert summary["rating_distribution_exact"] == {"3.50": 1}
