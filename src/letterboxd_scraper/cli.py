"""Command-line interface for reproducible Letterboxd scrape configurations."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from letterboxd_scraper import __version__
from letterboxd_scraper.config import load_config
from letterboxd_scraper.exceptions import LetterboxdScraperError
from letterboxd_scraper.pipeline import ScrapePipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="letterboxd-toolkit",
        description="Build validated Letterboxd import and audit datasets from public lists.",
    )
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        help="Path to a TOML scrape configuration.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config is None:
        parser.error("the following arguments are required: config")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
        result = ScrapePipeline(config).run()
    except (OSError, LetterboxdScraperError, ValueError) as exc:
        logging.getLogger(__name__).error("Scrape failed: %s", exc)
        return 1

    print(f"Selected films: {len(result.selected)}")
    print(f"Import CSV: {result.output_paths.import_csv}")
    if result.output_paths.audit_csv:
        print(f"Audit CSV: {result.output_paths.audit_csv}")
    if result.output_paths.summary_json:
        print(f"Summary JSON: {result.output_paths.summary_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
