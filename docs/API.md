# Python API

The distribution is named `letterboxd-list-toolkit`. The import package remains
`letterboxd_scraper` for compatibility with the original project.

## High-level API

Use `build_list` when a TOML configuration file is unnecessary:

```python
from letterboxd_scraper import build_list

result = build_list(
    seed_lists=[
        "https://letterboxd.com/user/list/example/",
    ],
    min_rating=3.0,
    max_rating=3.5,
    filters=("hide-tv", "hide-shorts", "hide-documentaries"),
    concurrency=8,
    output_directory="output/example",
    basename="example",
)

print(f"Selected {len(result.selected)} films")
print(result.output_paths.import_csv)
```

The run writes the configured outputs and returns all in-memory records:

- `result.candidates`: canonical references after list algebra;
- `result.resolved`: films with resolved metadata;
- `result.unresolved`: records that could not be fully resolved;
- `result.selected`: films that passed the rating filters;
- `result.list_results`: per-list diagnostics;
- `result.output_paths`: paths written by the pipeline.

A completed result can be exported again without another network request:

```python
result.to_letterboxd_csv("exports/import.csv")
result.to_audit_csv("exports/audit.csv")
```

## List algebra

The high-level function applies the same model as the CLI:

```text
union(seed_lists)
∩ include_lists[0]
∩ include_lists[1]
...
− union(exclude_lists)
```

```python
from letterboxd_scraper import build_list

result = build_list(
    seed_lists=["https://letterboxd.com/user/list/all-films/"],
    include_lists=["https://letterboxd.com/user/list/over-10k/"],
    exclude_lists=["https://letterboxd.com/user/list/over-100k/"],
)
```

## Advanced API

Construct `AppConfig` directly when every transport, cache, validation, and output
setting must be explicit:

```python
from pathlib import Path

from letterboxd_scraper import (
    AppConfig,
    CacheConfig,
    HttpConfig,
    OutputConfig,
    QueryConfig,
    ScrapePipeline,
    ValidationConfig,
)

config = AppConfig(
    query=QueryConfig(
        seed_lists=("https://letterboxd.com/user/list/example/",),
        min_rating=3.0,
        max_rating=3.5,
    ),
    http=HttpConfig(concurrency=8),
    cache=CacheConfig(directory=Path(".cache/letterboxd")),
    validation=ValidationConfig(expected_min_candidates=100),
    output=OutputConfig(directory=Path("output"), basename="example"),
)

result = ScrapePipeline(config).run()
```

## Standalone exporters

The exporters accept resolved `FilmDetails` objects:

```python
from letterboxd_scraper import write_audit_csv, write_letterboxd_csv

write_letterboxd_csv("movies.csv", result.selected)
write_audit_csv("movies_audit.csv", result.selected)
```

## Exceptions

All package-specific exceptions inherit from `LetterboxdScraperError`:

- `ConfigurationError` for invalid or contradictory configuration;
- `FetchError` for exhausted HTTP retries;
- `ParseError` for required data that cannot be parsed;
- `ValidationError` for suspicious or incomplete datasets.

```python
from letterboxd_scraper import LetterboxdScraperError, build_list

try:
    result = build_list(seed_lists=["https://letterboxd.com/user/list/example/"])
except LetterboxdScraperError as exc:
    print(f"The dataset was not generated safely: {exc}")
```

## Stability policy

The documented imports from `letterboxd_scraper.__init__` form the public API.
Modules, functions, and classes not re-exported there should be treated as internal
until documented otherwise.

Versions before `1.0` may introduce compatibility changes, but they will be recorded
in the changelog and released with semantic versioning.
