# Architecture

The package is organized around narrow responsibilities and pure transformations
where practical.

## Public API boundary

The supported library surface is re-exported from `letterboxd_scraper.__init__`.
Consumers should prefer those imports instead of depending on internal modules.

Two entry levels are available:

- `build_list(...)` for a concise Python API;
- `AppConfig` plus `ScrapePipeline` for complete control.

The distribution is named `letterboxd-list-toolkit`, while the import package
remains `letterboxd_scraper` for backward compatibility.

## Modules

### `api.py`

Builds a complete typed configuration from normal Python arguments, validates
common contradictions, and executes the pipeline. It is intentionally thin so the
CLI and Python API use the same orchestration and safety checks.

### `config.py`

Loads TOML into immutable dataclasses and rejects contradictory or malformed
settings before HTTP work starts.

### `http.py`

Owns transport concerns:

- one `requests.Session` per worker thread;
- retryable status classification;
- exponential backoff with jitter;
- global request pacing;
- Jina Reader fallback for public pages.

No parsing logic belongs here.

### `parsing.py`

Contains pure parsers for:

- Letterboxd list HTML;
- Jina Reader markdown;
- film HTML;
- film markdown fallback.

It recognizes both known average-rating text formats:

```text
3.90 avg rating
3.90 out of 5
```

It also reads JSON-LD `aggregateRating.ratingValue` when available. Markdown list
parsing is deliberately restricted to poster rows so incidental `/film/` links do
not become false-positive records.

### `list_scraper.py`

Owns list pagination and page-layout variants. It tries detail and grid layouts
while preserving country and language filters in their required path order.

A `404` after valid pages is treated as normal pagination termination.

### `film_resolver.py`

Resolves candidate films concurrently. It uses the cache first, tries the direct
film page, then uses Jina Reader as a best-effort fallback. A single failed film
becomes an unresolved record instead of aborting the entire run.

### `cache.py`

Stores one atomic JSON file per canonical film URI. The URI is hashed so filenames
remain portable. Cache entries expire according to the configured TTL.

### `pipeline.py`

Coordinates the use case:

1. scrape all configured lists;
2. apply set algebra;
3. validate candidate volume;
4. resolve films;
5. validate unresolved ratio;
6. apply rating boundaries;
7. validate and sort the output;
8. write exports.

`ScrapeResult` retains all in-memory stages and exposes convenience methods for
writing import and audit CSVs again without another scrape.

### `validation.py`

Centralizes invariants that prevent silent corruption:

- candidate count ranges;
- unresolved-ratio limit;
- non-empty output;
- unique canonical URIs.

### `output.py`

Writes deterministic UTF-8-with-BOM CSV files for spreadsheet and Letterboxd
compatibility, plus JSON diagnostics. Public exporter functions can also be used
independently with resolved `FilmDetails` records.

## Dependency direction

```text
Python API ─┐
            ├── Pipeline
CLI ────────┘    ├── ListScraper ── HTTP ── requests
                 ├── FilmResolver ── Cache
                 ├── Validation
                 └── Output

Parsing and models remain independent of orchestration.
```

## Distribution boundary

The wheel contains only the reusable `letterboxd_scraper` package and its
`py.typed` marker. Operational scripts and repository workflows are not imported
by library consumers.

CI builds both wheel and source distributions, validates metadata, installs the
wheel into an isolated environment, and smoke-tests the Python import and both CLI
entry points.

## Extension points

New data sources should implement adapters that produce `dict[str, FilmRef]`. New
metadata parsers should return `FilmDetails`. Keeping those interfaces stable
allows the list algebra, filtering, validation, and output layers to remain
unchanged.
