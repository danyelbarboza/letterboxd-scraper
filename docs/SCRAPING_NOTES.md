# Scraping Notes and Failure Modes

This document records the operational knowledge that motivated the implementation.

## 1. Public list pages may return HTTP 403

Letterboxd can serve public pages in a browser while returning `403` to a hosted runner. Retrying the same request indefinitely is not useful.

The scraper therefore:

1. retries transient direct failures with exponential backoff and jitter;
2. uses Jina Reader as a read-only fallback for public list pages;
3. validates the candidate count so a blocked response cannot silently become an empty dataset.

## 2. Jina Reader must receive the HTTPS target

Use:

```text
https://r.jina.ai/https://letterboxd.com/...
```

Using an HTTP target can return a security-verification document instead of the requested content. The client also checks for known verification-page text.

## 3. Pagination can end with HTTP 404

Letterboxd list pagination frequently returns `404` for the first page beyond the end. This is normal termination after at least one valid page, not necessarily a scrape failure.

The list scraper also stops when a page produces no new canonical film URLs, preventing accidental loops.

## 4. List page formats vary

Two page patterns have been observed:

```text
/list-slug/detail/page/2/
/list-slug/page/2/
```

The scraper tries both. The HTML parser supports current and legacy poster attributes, while the markdown parser accepts both absolute and relative film links.

## 5. The average-rating wording changed

A parser that only recognized:

```text
3.90 avg rating
```

started treating every film as unresolved after Letterboxd changed the metadata text to:

```text
3.90 out of 5
```

The current parser recognizes both forms and also checks JSON-LD. Tests cover this regression explicitly.

## 6. Canonical URLs are the primary key

Titles are not stable or unique. Alternate titles, punctuation changes, remakes, and duplicated names make title-based deduplication unsafe.

Every set operation and output uniqueness check therefore uses the canonical URL:

```text
https://letterboxd.com/film/<slug>/
```

## 7. Watch thresholds are usually snapshots

Letterboxd does not expose a public official endpoint for arbitrary watch-count queries. Large popularity-based datasets commonly rely on community-maintained lists such as “over 500k watched.”

Consequences:

- the watch threshold is only as current as the source list;
- the rating is current at scrape time;
- the output count should be described as “found in this snapshot,” not an absolute count of all films on Letterboxd.

The summary JSON records all source URLs and scrape diagnostics for this reason.

## 8. Validate expected magnitude

A successful process exit is not enough. During development, a workflow completed successfully while the source list had actually returned no usable films.

Use candidate guards based on known source-list size:

```toml
expected_min_candidates = 1200
expected_max_candidates = 1600
```

The range should be generous enough for legitimate growth but narrow enough to detect blocks, parser breakage, and accidental inclusion of unrelated links.

## 9. Separate import data from audit data

The Letterboxd import file should remain minimal:

```csv
Title,Year,LetterboxdURI
```

Operational metadata belongs in a separate audit file. This keeps imports reliable without sacrificing traceability.

## 10. Be conservative with concurrency

Concurrency improves a 1,500-film scrape substantially, but aggressive request volume increases blocking risk. The default combines bounded worker count, shared pacing, retries, caching, and a TTL.

For repeated experiments, reuse the cache instead of scraping every film page again.
