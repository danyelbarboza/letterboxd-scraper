# Letterboxd List Toolkit

[![CI](https://github.com/danyelbarboza/letterboxd-list-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/danyelbarboza/letterboxd-list-toolkit/actions/workflows/ci.yml)

A typed Python library and CLI for building reproducible, validated, and auditable
Letterboxd list datasets from public lists.

The toolkit combines resilient scraping, list algebra, rating filters, caching,
concurrency, validation, deterministic CSV exports, and production-sized workflows
such as Top 10 films by country and official language.

> This project is not affiliated with Letterboxd. It works with public pages and
> should be used conservatively and responsibly.

## Why this project exists

Large Letterboxd exports fail in ways that can look successful:

- public pages may return HTTP `403` from hosted runners;
- pagination often ends with `404` instead of an empty page;
- filtered-list URLs have a strict path order;
- broad markdown link extraction can turn footer or comment links into films;
- ratings and page markup change over time;
- a green workflow can still produce an empty or incomplete CSV;
- popularity criteria often come from community-maintained snapshot lists.

The toolkit encodes the lessons from real production-sized runs as parsers,
validation gates, tests, diagnostics, and repeatable workflows.

## Features

- Python API and command-line interface.
- Scrape one or more public Letterboxd lists.
- Union seed lists, intersect inclusion lists, and subtract exclusion lists.
- Apply inclusive or exclusive rating boundaries.
- Hide TV, shorts, and documentaries through Letterboxd filters.
- Resolve title, year, canonical URL, and current average rating.
- Use Jina Reader as a read-only fallback when direct list pages are unavailable.
- Cache film metadata and resolve records concurrently.
- Export Letterboxd-compatible, audit, unresolved, and summary files.
- Abort on suspicious candidate counts, excessive unresolved records, duplicates,
  or empty output.
- Generate country and official-language datasets with an eight-shard GitHub
  Actions workflow.
- Ship inline type information for editors and type checkers.

## Installation

### From GitHub

Until the first PyPI release is published:

```bash
python -m pip install "git+https://github.com/danyelbarboza/letterboxd-list-toolkit.git"
```

For the optional country and language workflow dependencies:

```bash
python -m pip install "letterboxd-list-toolkit[country] @ git+https://github.com/danyelbarboza/letterboxd-list-toolkit.git"
```

### From PyPI

After the first tagged release:

```bash
python -m pip install letterboxd-list-toolkit
```

The distribution is named `letterboxd-list-toolkit`. The Python import package
remains `letterboxd_scraper` for compatibility.

## Python quick start

```python
from letterboxd_scraper import build_list

result = build_list(
    seed_lists=[
        "https://letterboxd.com/user/list/example/",
    ],
    min_rating=3.0,
    max_rating=3.5,
    concurrency=8,
    output_directory="output/example",
    basename="example",
)

print(f"Selected {len(result.selected)} films")
print(result.output_paths.import_csv)
```

The returned result keeps all records in memory:

```python
result.candidates
result.resolved
result.unresolved
result.selected
result.list_results
result.output_paths
```

A completed result can be exported again without making another network request:

```python
result.to_letterboxd_csv("exports/movies.csv")
result.to_audit_csv("exports/movies_audit.csv")
```

See the complete [Python API guide](docs/API.md).

## CLI quick start

Create a TOML configuration and run:

```bash
letterboxd-toolkit examples/popular-not-beloved.toml
```

The legacy command remains available:

```bash
letterboxd-scraper examples/popular-not-beloved.toml
```

Both commands execute the same pipeline.

## Configuration

```toml
[query]
seed_lists = [
  "https://letterboxd.com/cinemageekyt/list/letterboxd-500k-watched-club/",
]
include_lists = []
exclude_lists = []
filters = ["hide-tv", "hide-shorts", "hide-documentaries"]
min_rating = 3.00
max_rating = 3.50
min_rating_inclusive = true
max_rating_inclusive = true
max_pages_per_list = 25

[http]
concurrency = 8
max_attempts = 6
timeout_seconds = 45
min_request_interval_seconds = 0.05
use_jina_fallback = true

[cache]
enabled = true
directory = ".cache/letterboxd"
ttl_hours = 24

[validation]
expected_min_candidates = 1200
expected_max_candidates = 1600
max_unresolved_ratio = 0.02
require_nonempty_output = true

[output]
directory = "output/popular-not-beloved"
basename = "popular_not_beloved"
include_audit_csv = true
include_unresolved_json = true
include_summary_json = true
```

## Outputs

A standard run can write:

```text
output/popular-not-beloved/
├── popular_not_beloved.csv
├── popular_not_beloved_audit.csv
├── popular_not_beloved_summary.json
└── popular_not_beloved_unresolved.json
```

The import CSV uses the columns accepted by Letterboxd:

```csv
Title,Year,LetterboxdURI
```

The audit CSV adds current rating and parser provenance.

## List algebra

The candidate set is calculated as:

```text
union(seed_lists)
∩ include_lists[0]
∩ include_lists[1]
...
− union(exclude_lists)
```

For example:

```text
all narrative films ∩ over-10k-watches − over-100k-watches
```

approximates a 10,000-to-100,000 watch-count band at the source lists' snapshot
dates.

See [`examples/watch-band.toml.example`](examples/watch-band.toml.example).

## Rating boundaries

Include exactly `3.00` and `3.50`:

```toml
min_rating = 3.00
max_rating = 3.50
min_rating_inclusive = true
max_rating_inclusive = true
```

Select ratings strictly above `3.50`:

```toml
min_rating = 3.50
min_rating_inclusive = false
```

## Country and official-language workflow

The repository contains a manual GitHub Actions workflow that:

1. discovers Letterboxd country and language filters;
2. maps official and regional official languages;
3. runs country processing across eight parallel shards;
4. combines shard outputs deterministically;
5. validates counts, canonical URLs, duplicates, and known false positives;
6. writes import, audit, country, language, unresolved, and continent outputs.

See [Country Export](docs/COUNTRY_EXPORT.md) and
[Validated Failure Modes](docs/VALIDATED_FAILURE_MODES.md).

## Validation philosophy

A scraper should fail loudly when its assumptions stop being true.

Candidate-count guards are especially important for community-maintained lists. A
blocked response, malformed filtered URL, or parser regression must not silently
become an empty dataset with a successful exit code.

Recommended validation:

```toml
[validation]
expected_min_candidates = 1200
expected_max_candidates = 1600
max_unresolved_ratio = 0.02
require_nonempty_output = true
```

Update expected counts when the source list legitimately changes.

## Data interpretation

The output is not an official Letterboxd database dump.

- **Popularity lists are snapshots.** Their counts depend on the maintainer's most
  recent update.
- **Ratings are collected at scrape time.** Films may later enter or leave a rating
  range.
- **Community lists can contain omissions or mistakes.** Inspect audit outputs.
- **Content filters depend on Letterboxd classifications.** Review samples before
  publishing a derived list.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,country]'
make check
```

Individual checks:

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest --cov=letterboxd_scraper --cov-report=term-missing
python -m build
twine check dist/*
```

CI validates Python 3.11 and 3.12, builds a wheel and source distribution, installs
the wheel in an isolated environment, and smoke-tests both command names.

## Releases

The package uses semantic versioning and records changes in
[CHANGELOG.md](CHANGELOG.md).

Publishing is prepared through GitHub Releases and PyPI Trusted Publishing:

1. configure a PyPI trusted publisher for this repository and the `pypi`
   environment;
2. create a release whose tag matches the package version, such as `v0.2.0`;
3. GitHub Actions builds, validates, and publishes the distributions.

## Responsible use

Use conservative concurrency, caching, and request intervals. Do not overload
Letterboxd or attempt to bypass authentication or access controls. Only process
public pages, respect applicable terms and local law, and prefer reproducible
snapshots over continuous high-frequency collection.

## Documentation

- [Python API](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Country Export](docs/COUNTRY_EXPORT.md)
- [Scraping Notes](docs/SCRAPING_NOTES.md)
- [Validated Failure Modes](docs/VALIDATED_FAILURE_MODES.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

MIT
