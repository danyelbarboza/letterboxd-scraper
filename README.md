# Letterboxd List Scraper

A reusable Python toolkit for building validated Letterboxd list datasets from public lists.

It was designed around real-world failure modes encountered while generating large importable lists:

- public Letterboxd list pages may return HTTP `403` from hosted runners;
- pagination commonly ends with HTTP `404`, not an empty `200` response;
- list markup changes over time;
- average-rating text changed from `avg rating` to `out of 5`;
- a workflow can appear successful while silently exporting an empty CSV;
- watch counts are usually available through community-maintained threshold lists rather than a public official API;
- ratings are live values, while popularity lists are snapshots maintained by users.

The project turns those lessons into a modular pipeline with retries, fallback parsing, list algebra, caching, concurrency, validation, and deterministic exports.

## Features

- Scrape one or more public Letterboxd lists.
- Union seed lists, intersect inclusion lists, and subtract exclusion lists.
- Hide TV, shorts, and documentaries through Letterboxd list filters.
- Filter by inclusive or exclusive minimum and maximum average ratings.
- Resolve title, year, canonical URL, and current average rating.
- Fall back to Jina Reader when direct list pages are blocked.
- Cache film metadata to reduce repeated requests.
- Export a Letterboxd import CSV and a richer audit CSV.
- Export unresolved records and a machine-readable summary.
- Abort on suspicious candidate counts, excessive unresolved records, duplicates, or empty output.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
```

## Quick start

Run the included example:

```bash
letterboxd-scraper examples/popular-not-beloved.toml
```

The command writes:

```text
output/popular-not-beloved/
├── popular_not_beloved.csv
├── popular_not_beloved_audit.csv
├── popular_not_beloved_summary.json
└── popular_not_beloved_unresolved.json
```

The import CSV uses the exact columns accepted by Letterboxd:

```csv
Title,Year,LetterboxdURI
```

## Configuration

Every run is defined by a TOML file.

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
concurrency = 10
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

## List algebra

The candidate set is calculated as:

```text
union(seed_lists)
∩ include_lists[0]
∩ include_lists[1]
...
− union(exclude_lists)
```

This makes popularity bands reproducible when maintained threshold lists exist.

For example:

```text
all narrative films ∩ over-10k-watches − over-100k-watches
```

approximates films with 10,000 to 100,000 watches at the threshold lists' snapshot dates.

See [`examples/watch-band.toml.example`](examples/watch-band.toml.example).

## Rating boundaries

Boundaries are explicit. To include exactly `3.00` and `3.50`:

```toml
min_rating = 3.00
max_rating = 3.50
min_rating_inclusive = true
max_rating_inclusive = true
```

To select only ratings strictly above `3.50`:

```toml
min_rating = 3.50
min_rating_inclusive = false
```

## Validation philosophy

A scraper should fail loudly when its assumptions stop being true.

Candidate-count guards are especially important for community-maintained popularity lists. A blocked page or parser regression should not produce an empty CSV and a green workflow.

Recommended configuration:

```toml
[validation]
expected_min_candidates = 1200
expected_max_candidates = 1600
max_unresolved_ratio = 0.02
require_nonempty_output = true
```

Update expected counts when the source list legitimately grows.

## Data interpretation

The output is not an official database dump.

- **Popularity criteria are snapshots.** A list such as “500k Watched Club” reflects the maintainer's latest update, not necessarily the current second-by-second state of Letterboxd.
- **Ratings are current at scrape time.** A film may move into or out of the configured range later.
- **Community lists can contain omissions or mistakes.** The audit CSV and summary exist so results can be inspected.
- **Content filters depend on Letterboxd classifications.** Always review a sample before publishing a list.

## Development

```bash
make install
make check
```

Or run checks individually:

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=letterboxd_scraper --cov-report=term-missing
```

## Responsible use

Use conservative concurrency, caching, and request intervals. Do not use the project to overload Letterboxd or bypass authentication. Only scrape public pages, respect applicable terms and local law, and prefer snapshots over continuous high-frequency collection.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Scraping notes and failure modes](docs/SCRAPING_NOTES.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT
