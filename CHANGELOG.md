# Changelog

All notable changes to this project will be documented in this file.

The project follows [Semantic Versioning](https://semver.org/) while it evolves
toward a stable `1.0` public API.

## [0.2.0] - 2026-07-19

### Added

- High-level `build_list(...)` Python API that does not require a TOML file.
- Public result exporters through `ScrapeResult.to_letterboxd_csv(...)` and
  `ScrapeResult.to_audit_csv(...)`.
- Public standalone `write_letterboxd_csv(...)` and `write_audit_csv(...)`
  functions.
- Distributed type information through `py.typed`.
- `letterboxd-toolkit` command-line entry point.
- Wheel and source-distribution validation in CI.
- Release workflow prepared for PyPI Trusted Publishing.
- Dedicated Python API documentation.

### Changed

- Distribution name changed from `letterboxd-list-scraper` to
  `letterboxd-list-toolkit`.
- Project status moved from Alpha to Beta.
- Package metadata now includes project URLs, keywords, and library classifiers.
- CLI help now presents the project as `letterboxd-toolkit`.

### Compatibility

- The Python import package remains `letterboxd_scraper`.
- The legacy `letterboxd-scraper` command remains available as an alias.
- Existing TOML configurations remain supported.

## [0.1.0] - 2026-07-19

### Added

- Modular Letterboxd list scraping pipeline.
- Direct HTML and Jina Reader parsing strategies.
- List algebra, rating filtering, caching, validation, and deterministic exports.
- Country and official-language Top 10 workflow with eight parallel shards.
- Regression tests for filtered URLs and poster-only markdown parsing.
