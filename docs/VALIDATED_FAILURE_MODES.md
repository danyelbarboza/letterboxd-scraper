# Validated failure modes

These findings came from the production-sized country export and are now encoded in parsers, tests, validation, or workflow design.

## Approaches that did not work reliably

1. **Appending `detail/page/1` after country and language filters.** The path order was wrong. First pages also do not require an explicit `page/1` segment.
2. **Extracting every `/film/` link from Jina markdown.** It captured unrelated footer and navigation links, especially on empty filtered pages.
3. **Assuming a successful process means valid data.** The first export completed while containing repeated false positives. Structural validation is mandatory.
4. **Resolving every film through direct Letterboxd first on hosted runners.** Repeated 403 responses made this unnecessarily slow. The country workflow uses a Jina-first resolver and direct HTML only as fallback.
5. **Running one large job.** Eight deterministic shards were materially easier to observe, retry, and consolidate.
6. **Treating missing ratings as missing films.** Title, year, and canonical URI can still be valid import metadata. Rating completeness is reported separately.
7. **Triggering the expensive export for every PR change.** Diagnostic branches created unnecessary queued runs. The maintained workflow is manual-only.

## Practices that worked

- exact country-language URLs;
- poster-row-only parsing;
- canonical URI deduplication;
- multilingual candidate merging before ranking;
- eight-way sharding;
- strict consolidation checks;
- separate import, audit, country summary, language summary, and diagnostic outputs;
- a known-country end-to-end sentinel;
- caching and deterministic sorting.

Any future parser or URL change should include a regression test based on one of these observed failures.
