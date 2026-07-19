"""Public API for building reproducible Letterboxd list datasets."""

from letterboxd_scraper.api import build_list
from letterboxd_scraper.config import (
    AppConfig,
    CacheConfig,
    HttpConfig,
    OutputConfig,
    QueryConfig,
    ValidationConfig,
    load_config,
)
from letterboxd_scraper.exceptions import (
    ConfigurationError,
    FetchError,
    LetterboxdScraperError,
    ParseError,
    ValidationError,
)
from letterboxd_scraper.models import FilmDetails, FilmRef, ListScrapeResult
from letterboxd_scraper.output import OutputPaths, write_audit_csv, write_letterboxd_csv
from letterboxd_scraper.pipeline import ScrapePipeline, ScrapeResult

__all__ = [
    "AppConfig",
    "CacheConfig",
    "ConfigurationError",
    "FetchError",
    "FilmDetails",
    "FilmRef",
    "HttpConfig",
    "LetterboxdScraperError",
    "ListScrapeResult",
    "OutputConfig",
    "OutputPaths",
    "ParseError",
    "QueryConfig",
    "ScrapePipeline",
    "ScrapeResult",
    "ValidationConfig",
    "ValidationError",
    "build_list",
    "load_config",
    "write_audit_csv",
    "write_letterboxd_csv",
]
__version__ = "0.2.0"
