from __future__ import annotations

import csv
import json
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

INPUT_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "output/shards")
OUTPUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "output/top10-by-country")
TOP_N = 10


def normalize_filename(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return "_".join(value.casefold().split()) or "unmapped"


def read_rows(filename: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(INPUT_ROOT.glob(f"**/{filename}")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def write_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def import_rows(audit_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    unique: dict[str, dict[str, object]] = {}
    for row in audit_rows:
        uri = row["LetterboxdURI"]
        if not uri:
            continue
        unique.setdefault(
            uri,
            {"Title": row["Title"], "Year": row["Year"], "LetterboxdURI": uri},
        )
    return sorted(
        unique.values(),
        key=lambda row: (
            str(row["Title"]).casefold(),
            str(row["Year"]),
            str(row["LetterboxdURI"]),
        ),
    )


def numeric(row: dict[str, str], field: str) -> int:
    try:
        return int(row.get(field, "0") or 0)
    except ValueError:
        return 0


def validate(audit: list[dict[str, str]], countries: list[dict[str, str]]) -> None:
    if not countries:
        raise RuntimeError(f"No country shard data found under {INPUT_ROOT}")
    if len(countries) < 200:
        raise RuntimeError(f"Suspicious country count: {len(countries)}")
    missing = [
        row
        for row in audit
        if not row.get("Title")
        or not row.get("Year")
        or not row.get("LetterboxdURI", "").startswith("https://letterboxd.com/film/")
    ]
    if missing:
        raise RuntimeError(f"Audit contains {len(missing)} rows without import metadata")
    by_country: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in audit:
        by_country[row["Country"]].append(row)
    for country in countries:
        name = country["Country"]
        expected = numeric(country, "FilmsSelected")
        actual = len(by_country[name])
        if expected != actual:
            raise RuntimeError(f"Country count mismatch for {name}: summary={expected}, audit={actual}")
        if actual > TOP_N:
            raise RuntimeError(f"More than {TOP_N} films selected for {name}: {actual}")
        uris = [row["LetterboxdURI"] for row in by_country[name]]
        if len(uris) != len(set(uris)):
            raise RuntimeError(f"Duplicate film within country {name}")
        ranks = sorted(numeric(row, "Rank") for row in by_country[name])
        if ranks != list(range(1, actual + 1)):
            raise RuntimeError(f"Non-contiguous ranks for {name}: {ranks}")
    brazil = by_country.get("Brazil", [])
    if len(brazil) != TOP_N or not any(
        row["LetterboxdURI"].endswith("/city-of-god/") for row in brazil
    ):
        raise RuntimeError("Brazil validation failed: expected ten films including City of God")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audit = read_rows("top_10_by_country_audit.csv")
    countries = read_rows("country_summary.csv")
    country_languages = read_rows("countries_and_languages.csv")
    unresolved = read_rows("unresolved_films.csv")
    unmapped = read_rows("unmapped_countries.csv")
    unmatched = read_rows("unmatched_languages.csv")
    validate(audit, countries)

    audit.sort(
        key=lambda row: (
            row["Continent"],
            row["Country"].casefold(),
            numeric(row, "Rank"),
            row["Title"].casefold(),
        )
    )
    countries.sort(key=lambda row: row["Country"].casefold())
    country_languages.sort(
        key=lambda row: (row["Country"].casefold(), row["Language"].casefold())
    )
    unresolved.sort(key=lambda row: (row["Country"].casefold(), row["LetterboxdURI"]))
    unmapped.sort(key=lambda row: row["Country"].casefold())
    unmatched.sort(key=lambda row: (row["Country"].casefold(), row["LanguageCode"]))

    audit_fields = [
        "Country","CountryCode","CountrySlug","Continent","Rank","Title","Year",
        "LetterboxdURI","AverageRating","MatchedLanguages","LanguageCodes",
        "BestPositionInLanguageFilter","SourceURLs","RatingSource","MetadataSource","Error",
    ]
    country_fields = [
        "Country","CountryCode","CountrySlug","Continent","LetterboxdCountryCount",
        "LanguageCodes","Languages","LanguageCount","CandidateCount","FilmsSelected",
        "FilmsWithImportMetadata","FilmsWithResolvedRating","Status","Errors",
    ]
    language_fields = [
        "Country","CountryCode","CountrySlug","Continent","LanguageCode","Language",
        "LanguageSlug","SourceURL","CandidatesRead","SelectedFromThisLanguage",
        "FilmsSelectedForCountry","TargetPerCountry","SourceCounts","Error",
    ]

    write_rows(OUTPUT_DIR / "top_10_by_country_audit.csv", audit_fields, audit)
    write_rows(OUTPUT_DIR / "country_summary.csv", country_fields, countries)
    write_rows(OUTPUT_DIR / "countries_and_languages.csv", language_fields, country_languages)
    write_rows(
        OUTPUT_DIR / "unresolved_films.csv",
        ["Country","Continent","Title","Year","LetterboxdURI","Error"],
        unresolved,
    )
    write_rows(
        OUTPUT_DIR / "unmapped_countries.csv",
        ["Country","CountrySlug","LetterboxdCountryCount","Reason"],
        unmapped,
    )
    write_rows(
        OUTPUT_DIR / "unmatched_languages.csv",
        ["Country","CountrySlug","LanguageCode","ExpectedLanguageName"],
        unmatched,
    )

    import_data = import_rows(audit)
    write_rows(
        OUTPUT_DIR / "top_10_by_country_letterboxd_import.csv",
        ["Title", "Year", "LetterboxdURI"],
        import_data,
    )

    by_continent: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in audit:
        by_continent[row["Continent"]].append(row)
    for continent, rows in sorted(by_continent.items()):
        write_rows(
            OUTPUT_DIR / "continents" / f"{normalize_filename(continent)}.csv",
            ["Title", "Year", "LetterboxdURI"],
            import_rows(rows),
        )

    summary = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_list": "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/",
        "target_per_country": TOP_N,
        "countries_discovered": len(countries),
        "countries_with_10": sum(numeric(row, "FilmsSelected") == TOP_N for row in countries),
        "countries_partial": sum(0 < numeric(row, "FilmsSelected") < TOP_N for row in countries),
        "countries_empty": sum(numeric(row, "FilmsSelected") == 0 for row in countries),
        "audit_rows": len(audit),
        "unique_import_films": len(import_data),
        "duplicate_country_assignments_removed_from_import": len(audit) - len(import_data),
        "country_language_rows": len(country_languages),
        "unmapped_countries": len(unmapped),
        "unmatched_country_language_codes": len(unmatched),
        "unresolved_records": len(unresolved),
        "continent_files": sorted(by_continent),
        "validation": "passed",
        "methodology": {
            "country_universe": "All country, territory, and historical-country filters listed by Letterboxd",
            "languages": "CLDR official, de-facto official, and official-regional languages matched to Letterboxd language filters",
            "selection": "At most ten films per country from exact country-language URLs; only poster rows are parsed; multilingual candidates are ranked by current Letterboxd average rating",
            "deduplication": "Import CSVs are unique by canonical Letterboxd URI; the audit CSV retains each country assignment",
        },
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
