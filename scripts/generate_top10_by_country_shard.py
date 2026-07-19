from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import generate_top10_by_country as common
from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig, HttpConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.models import FilmDetails, FilmRef

POSTER_LINE = re.compile(
    r"^\*\s+!\[Image\s+\d+:\s+Poster for .*?\]\([^\n]+\)"
    r"\[(?P<title>.+)\s+\((?P<year>(?:18|19|20|21)\d{2})\)\]"
    r"\((?P<uri>https://letterboxd\.com/film/[^)]+/)\)\s*$",
    re.MULTILINE,
)


def parse_poster_references(markdown: str, limit: int) -> list[FilmRef]:
    films: list[FilmRef] = []
    seen: set[str] = set()
    for match in POSTER_LINE.finditer(markdown):
        uri = match.group("uri")
        if uri in seen:
            continue
        seen.add(uri)
        films.append(
            FilmRef(
                uri=uri,
                title=match.group("title").strip(),
                year=int(match.group("year")),
            )
        )
        if len(films) == limit:
            break
    return films


def fetch_candidates(http: HttpClient, source_url: str) -> tuple[list[FilmRef], dict[str, int]]:
    response = http.get_jina(source_url)
    return parse_poster_references(response.text, common.TOP_N), {"jina-poster-markdown": 1}


def main() -> int:
    shard_index = int(os.environ.get("SHARD_INDEX", "0"))
    shard_count = int(os.environ.get("SHARD_COUNT", "1"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", f"output/country-shard-{shard_index}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("SHARD_INDEX must be between 0 and SHARD_COUNT - 1")

    http = HttpClient(
        HttpConfig(
            timeout_seconds=45,
            max_attempts=3,
            backoff_base_seconds=0.75,
            max_backoff_seconds=8,
            concurrency=24,
            min_request_interval_seconds=0.08,
            use_jina_fallback=True,
        )
    )
    resolver = FilmResolver(
        http,
        FilmCache(
            CacheConfig(
                enabled=True,
                directory=Path(".cache/letterboxd-country"),
                ttl_hours=168,
            )
        ),
        concurrency=24,
    )

    countries, languages = common.filter_options(http)
    language_index = common.language_lookup(languages)
    all_countries = sorted(countries.values(), key=lambda item: item["name"].casefold())
    selected_countries = [
        country
        for position, country in enumerate(all_countries)
        if position % shard_count == shard_index
    ]

    audit_rows: list[dict[str, object]] = []
    country_rows: list[dict[str, object]] = []
    language_rows: list[dict[str, object]] = []
    unresolved_rows: list[dict[str, object]] = []
    unmapped_rows: list[dict[str, object]] = []
    unmatched_rows: list[dict[str, object]] = []

    print(
        f"Shard {shard_index}/{shard_count}: {len(selected_countries)} of "
        f"{len(all_countries)} countries",
        flush=True,
    )

    for local_index, country_option in enumerate(selected_countries, 1):
        country = str(country_option["name"])
        country_code = common.alpha2(country)
        continent = common.continent(country, country_code)
        if not country_code and country not in common.HISTORICAL:
            unmapped_rows.append(
                {
                    "Country": country,
                    "CountrySlug": country_option["slug"],
                    "LetterboxdCountryCount": country_option["count"],
                    "Reason": "No ISO/CLDR country mapping",
                }
            )

        matched_languages: list[tuple[str, dict[str, object]]] = []
        unmatched_codes: list[str] = []
        seen_language_slugs: set[str] = set()
        for language_code in common.language_codes(country, country_code):
            option = common.match_language(language_code, languages, language_index)
            if not option:
                unmatched_codes.append(language_code)
                unmatched_rows.append(
                    {
                        "Country": country,
                        "CountrySlug": country_option["slug"],
                        "LanguageCode": language_code,
                        "ExpectedLanguageName": common.language_name(language_code),
                    }
                )
                continue
            language_slug = str(option["slug"])
            if language_slug not in seen_language_slugs:
                matched_languages.append((language_code, option))
                seen_language_slugs.add(language_slug)

        runs: list[dict[str, object]] = []
        for language_code, option in matched_languages:
            source_url = urljoin(
                common.BASE,
                f"country/{country_option['slug']}/language/{option['slug']}/",
            )
            try:
                references, source_counts = fetch_candidates(http, source_url)
                error = ""
            except Exception as exc:
                references, source_counts, error = [], {}, repr(exc)
            runs.append(
                {
                    "code": language_code,
                    "option": option,
                    "source": source_url,
                    "references": references,
                    "source_counts": source_counts,
                    "error": error,
                }
            )

        candidates: dict[str, dict[str, object]] = {}
        for run in runs:
            for position, reference in enumerate(run["references"], 1):
                candidate = candidates.setdefault(
                    reference.uri,
                    {
                        "reference": reference,
                        "position": position,
                        "languages": set(),
                        "language_codes": set(),
                        "sources": set(),
                    },
                )
                candidate["reference"] = candidate["reference"].merge(reference)
                candidate["position"] = min(int(candidate["position"]), position)
                candidate["languages"].add(run["option"]["name"])
                candidate["language_codes"].add(run["code"])
                candidate["sources"].add(run["source"])

        references_by_uri = {
            uri: candidate["reference"] for uri, candidate in candidates.items()
        }
        resolved, unresolved = (
            resolver.resolve_many(references_by_uri) if references_by_uri else ([], [])
        )
        unresolved_rows.extend(
            {
                "Country": country,
                "Continent": continent,
                "Title": item.title,
                "Year": item.year,
                "LetterboxdURI": item.uri,
                "Error": item.error,
            }
            for item in unresolved
        )
        details_by_uri = {item.uri: item for item in resolved + unresolved}

        ranked: list[tuple[FilmDetails, dict[str, object]]] = []
        for uri, candidate in candidates.items():
            reference = candidate["reference"]
            details = details_by_uri.get(uri) or FilmDetails(
                uri=uri,
                title=reference.title,
                year=reference.year,
                average_rating=None,
                error="No resolver record",
            )
            ranked.append((details, candidate))
        ranked.sort(
            key=lambda pair: (
                pair[0].average_rating is None,
                -(pair[0].average_rating or -1),
                int(pair[1]["position"]),
                pair[0].title.casefold(),
                pair[0].uri,
            )
        )
        selected = ranked[: common.TOP_N]

        for rank, (details, candidate) in enumerate(selected, 1):
            reference = candidate["reference"]
            audit_rows.append(
                {
                    "Country": country,
                    "CountryCode": country_code or "",
                    "CountrySlug": country_option["slug"],
                    "Continent": continent,
                    "Rank": rank,
                    "Title": details.title or reference.title,
                    "Year": details.year or reference.year,
                    "LetterboxdURI": details.uri,
                    "AverageRating": details.average_rating,
                    "MatchedLanguages": "; ".join(sorted(candidate["languages"])),
                    "LanguageCodes": "; ".join(sorted(candidate["language_codes"])),
                    "BestPositionInLanguageFilter": candidate["position"],
                    "SourceURLs": "; ".join(sorted(candidate["sources"])),
                    "RatingSource": details.rating_source,
                    "MetadataSource": details.metadata_source,
                    "Error": details.error,
                }
            )

        selected_count = len(selected)
        country_rows.append(
            {
                "Country": country,
                "CountryCode": country_code or "",
                "CountrySlug": country_option["slug"],
                "Continent": continent,
                "LetterboxdCountryCount": country_option["count"],
                "LanguageCodes": "; ".join(code for code, _ in matched_languages),
                "Languages": "; ".join(str(option["name"]) for _, option in matched_languages),
                "LanguageCount": len(matched_languages),
                "CandidateCount": len(candidates),
                "FilmsSelected": selected_count,
                "FilmsWithImportMetadata": sum(
                    1 for details, _ in selected if details.title and details.year
                ),
                "FilmsWithResolvedRating": sum(
                    1 for details, _ in selected if details.average_rating is not None
                ),
                "Status": "ok"
                if selected_count == common.TOP_N
                else ("partial" if selected_count else "empty"),
                "Errors": " | ".join(str(run["error"]) for run in runs if run["error"]),
            }
        )

        selected_uris = {details.uri for details, _ in selected}
        for run in runs:
            run_uris = {reference.uri for reference in run["references"]}
            language_rows.append(
                {
                    "Country": country,
                    "CountryCode": country_code or "",
                    "CountrySlug": country_option["slug"],
                    "Continent": continent,
                    "LanguageCode": run["code"],
                    "Language": run["option"]["name"],
                    "LanguageSlug": run["option"]["slug"],
                    "SourceURL": run["source"],
                    "CandidatesRead": len(run["references"]),
                    "SelectedFromThisLanguage": len(selected_uris & run_uris),
                    "FilmsSelectedForCountry": selected_count,
                    "TargetPerCountry": common.TOP_N,
                    "SourceCounts": json.dumps(run["source_counts"], sort_keys=True),
                    "Error": run["error"],
                }
            )

        for language_code in unmatched_codes:
            language_rows.append(
                {
                    "Country": country,
                    "CountryCode": country_code or "",
                    "CountrySlug": country_option["slug"],
                    "Continent": continent,
                    "LanguageCode": language_code,
                    "Language": common.language_name(language_code),
                    "LanguageSlug": "",
                    "SourceURL": "",
                    "CandidatesRead": 0,
                    "SelectedFromThisLanguage": 0,
                    "FilmsSelectedForCountry": selected_count,
                    "TargetPerCountry": common.TOP_N,
                    "SourceCounts": "{}",
                    "Error": "No matching Letterboxd language filter",
                }
            )
        if not runs and not unmatched_codes:
            language_rows.append(
                {
                    "Country": country,
                    "CountryCode": country_code or "",
                    "CountrySlug": country_option["slug"],
                    "Continent": continent,
                    "LanguageCode": "",
                    "Language": "",
                    "LanguageSlug": "",
                    "SourceURL": "",
                    "CandidatesRead": 0,
                    "SelectedFromThisLanguage": 0,
                    "FilmsSelectedForCountry": selected_count,
                    "TargetPerCountry": common.TOP_N,
                    "SourceCounts": "{}",
                    "Error": "No matched Letterboxd language filter",
                }
            )

        print(
            f"[{local_index:03d}/{len(selected_countries):03d}] {country}: "
            f"{selected_count}/{common.TOP_N}, {len(matched_languages)} language(s), "
            f"{len(candidates)} candidates",
            flush=True,
        )

    common.write_csv(
        output_dir / "top_10_by_country_audit.csv",
        [
            "Country","CountryCode","CountrySlug","Continent","Rank","Title","Year",
            "LetterboxdURI","AverageRating","MatchedLanguages","LanguageCodes",
            "BestPositionInLanguageFilter","SourceURLs","RatingSource","MetadataSource","Error",
        ],
        audit_rows,
    )
    common.write_csv(
        output_dir / "country_summary.csv",
        [
            "Country","CountryCode","CountrySlug","Continent","LetterboxdCountryCount",
            "LanguageCodes","Languages","LanguageCount","CandidateCount","FilmsSelected",
            "FilmsWithImportMetadata","FilmsWithResolvedRating","Status","Errors",
        ],
        country_rows,
    )
    common.write_csv(
        output_dir / "countries_and_languages.csv",
        [
            "Country","CountryCode","CountrySlug","Continent","LanguageCode","Language",
            "LanguageSlug","SourceURL","CandidatesRead","SelectedFromThisLanguage",
            "FilmsSelectedForCountry","TargetPerCountry","SourceCounts","Error",
        ],
        language_rows,
    )
    common.write_csv(
        output_dir / "unresolved_films.csv",
        ["Country","Continent","Title","Year","LetterboxdURI","Error"],
        unresolved_rows,
    )
    common.write_csv(
        output_dir / "unmapped_countries.csv",
        ["Country","CountrySlug","LetterboxdCountryCount","Reason"],
        unmapped_rows,
    )
    common.write_csv(
        output_dir / "unmatched_languages.csv",
        ["Country","CountrySlug","LanguageCode","ExpectedLanguageName"],
        unmatched_rows,
    )

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "shard_index": shard_index,
        "shard_count": shard_count,
        "countries_total": len(all_countries),
        "countries_processed": len(selected_countries),
        "audit_rows": len(audit_rows),
    }
    (output_dir / "shard_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
