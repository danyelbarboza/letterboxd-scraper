# Top films by country and official language

This workflow generates up to ten highly rated films for every country, territory, and historical-country filter exposed by Letterboxd. It combines each country with its official or de-facto official languages, keeps the country assignments for auditing, and produces deduplicated Letterboxd import files.

## Run it

Open **Actions → Generate top 10 films by country → Run workflow**.

The workflow deliberately runs only through `workflow_dispatch`. A full export performs hundreds of public requests and should not run on every push or pull request.

Eight GitHub Actions jobs process deterministic country shards in parallel. A final job downloads every shard, combines the CSV files, validates the result, and publishes the `top-10-films-by-country` artifact.

## Outputs

- `top_10_by_country_letterboxd_import.csv`: unique films, ready for Letterboxd import.
- `top_10_by_country_audit.csv`: one row per country-film assignment, including rank, rating, matched language, and source URL.
- `country_summary.csv`: completeness and diagnostics for every country.
- `countries_and_languages.csv`: every attempted country-language combination and its result.
- `continents/*.csv`: deduplicated import files by continent.
- `unresolved_films.csv`, `unmapped_countries.csv`, and `unmatched_languages.csv`: explicit diagnostics.
- `summary.json`: counts, methodology, and validation status.

## Validated decisions

### Use exact filtered URLs

The working URL shape is:

```text
/list/<slug>/country/<country>/language/<language>/
```

For detail pages, `detail` belongs immediately after the list slug, before the country and language filters. Moving `detail` to the end changes the resource and can return unrelated content.

### Parse poster rows, not every film link

Jina-rendered pages contain links outside the actual list. Empty filtered pages may still include footer, navigation, recommendation, or comment links. A broad `/film/` regular expression produced convincing but invalid rows.

The package parser and the country exporter therefore accept only structured poster rows. An empty page is allowed to produce zero films.

### Rank multilingual candidates after merging

For countries with more than one official language, the workflow reads each exact language filter, merges candidates by canonical Letterboxd URI, and then orders them by the current average rating. Taking ten from the first language would bias the result.

### Keep import and audit semantics separate

Coproductions can legitimately appear for multiple countries. The audit file preserves those assignments. Import files are deduplicated by canonical URI so Letterboxd receives one row per film.

### Treat ratings and metadata independently

A film can have valid title, year, and URI even when the current rating cannot be resolved. Import metadata should be preserved and the missing rating recorded, rather than silently discarding the film.

## Validation gates

The combine step fails when:

- fewer than 200 country records are present;
- title, year, or canonical URI is missing from an audit row;
- a country contains more than ten selected films;
- a country contains duplicate URIs;
- ranks are not contiguous;
- country summary counts disagree with audit rows;
- the Brazil smoke check does not return ten films including `City of God`.

The Brazil check is not intended to define the global ranking forever. It is a stable end-to-end sentinel for the exact country-language route that originally validated the workflow.

## Operational guidance

- Keep the default eight shards unless runner availability becomes a problem.
- Reuse the metadata cache when running locally.
- Do not turn the workflow into a frequent schedule; popularity lists and ratings do not require high-frequency scraping.
- Review `country_summary.csv` before publishing. Zero or partial results can be legitimate for countries with little qualifying cinema, but sudden widespread emptiness indicates blocking or a parser change.
