from pathlib import Path

import pytest

from letterboxd_scraper.config import load_config
from letterboxd_scraper.exceptions import ConfigurationError


def test_load_config_reads_boundaries_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[query]
seed_lists = ["https://letterboxd.com/user/list/example/"]
min_rating = 3.0
max_rating = 3.5

[output]
directory = "custom-output"
basename = "example"
""",
        encoding="utf-8",
    )

    config = load_config(path)
    assert config.query.min_rating == 3.0
    assert config.query.max_rating == 3.5
    assert config.query.filters == ("hide-tv", "hide-shorts", "hide-documentaries")
    assert config.output.directory == Path("custom-output")


def test_load_config_rejects_inverted_rating_range(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[query]
seed_lists = ["https://letterboxd.com/user/list/example/"]
min_rating = 4.0
max_rating = 3.0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="cannot be greater"):
        load_config(path)
