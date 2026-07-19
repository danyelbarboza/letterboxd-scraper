from __future__ import annotations

import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from typing import Any

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://letterboxd.com/danyel/list/the-other-20th-century/"
ATLAS_IMPORT = Path("atlas/top_10_by_country_letterboxd_import.csv")
ATLAS_AUDIT = Path("atlas/top_10_by_country_audit.csv")
RATINGS = Path("input/ratings.csv")
OUT = Path("output/ratings_progress.json")

USER_AGENT = "Mozilla/5.0 (compatible; LetterboxdListToolkit/1.0)"


def slug_from_letterboxd_url(value: str) -> str | None:
    match = re.search(r"https?://(?:www\.)?letterboxd\.com/film/([^/?#]+)/?", value)
    return match.group(1) if match else None


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def scrape_public_list() -> dict[str, dict[str, Any]]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    films: dict[str, dict[str, Any]] = {}

    for page in range(1, 21):
        url = LIST_URL if page == 1 else f"{LIST_URL}page/{page}/"
        response = session.get(url, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        before = len(films)
        for node in soup.select("[data-film-slug], [data-target-link], a[href*='/film/']"):
            slug = node.get("data-film-slug")
            href = node.get("data-target-link") or node.get("href") or ""
            if not slug:
                match = re.search(r"/film/([^/]+)/", href)
                slug = match.group(1) if match else None
            if not slug or slug in films:
                continue

            title = (
                node.get("data-film-name")
                or node.get("alt")
                or node.get("title")
                or slug
            )
            year_value = node.get("data-film-release-year")
            try:
                year = int(year_value) if year_value else None
            except ValueError:
                year = None

            films[slug] = {"slug": slug, "title": title, "year": year}

        next_link = soup.select_one("a.next") or soup.find(
            "a", string=re.compile("Next", re.I)
        )
        if not next_link:
            break
        if len(films) == before:
            raise RuntimeError(f"No films found on paginated page {page}")

    return films


def resolve_short_uri(row: dict[str, str]) -> dict[str, Any]:
    uri = row.get("Letterboxd URI", "").strip()
    result: dict[str, Any] = {
        "name": row.get("Name", "").strip(),
        "year": int(row["Year"]) if row.get("Year", "").strip().isdigit() else None,
        "rating": float(row["Rating"]) if row.get("Rating", "").strip() else None,
        "date": row.get("Date", "").strip(),
        "source_uri": uri,
        "slug": None,
        "final_url": None,
        "error": None,
    }

    direct_slug = slug_from_letterboxd_url(uri)
    if direct_slug:
        result["slug"] = direct_slug
        result["final_url"] = uri
        return result

    headers = {"User-Agent": USER_AGENT}
    last_error: str | None = None
    for attempt in range(3):
        try:
            response = requests.get(
                uri,
                headers=headers,
                timeout=30,
                allow_redirects=True,
            )
            response.raise_for_status()
            final_url = response.url
            slug = slug_from_letterboxd_url(final_url)

            if not slug:
                match = re.search(
                    r'https?://(?:www\.)?letterboxd\.com/film/([^/"?#]+)/?',
                    response.text,
                )
                slug = match.group(1) if match else None

            if slug:
                result["slug"] = slug
                result["final_url"] = final_url
                return result

            last_error = f"No canonical film URL found; final URL: {final_url}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.8 * (attempt + 1))

    result["error"] = last_error
    return result


def resolve_ratings(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(resolve_short_uri, row) for row in rows]
        for future in as_completed(futures):
            resolved.append(future.result())
    return sorted(resolved, key=lambda item: (item["date"], item["name"]))


def rounded_percent(numerator: int, denominator: int) -> float:
    return round((100 * numerator / denominator), 2) if denominator else 0.0


def main() -> None:
    atlas_rows = load_csv(ATLAS_IMPORT)
    audit_rows = load_csv(ATLAS_AUDIT)
    rating_rows = load_csv(RATINGS)

    atlas = {
        slug: row
        for row in atlas_rows
        if (slug := slug_from_letterboxd_url(row.get("LetterboxdURI", "")))
    }
    other = scrape_public_list()
    resolved_ratings = resolve_ratings(rating_rows)

    resolved_by_slug = {
        item["slug"]: item
        for item in resolved_ratings
        if item.get("slug")
    }
    watched = set(resolved_by_slug)
    atlas_slugs = set(atlas)
    other_slugs = set(other)
    shared_slugs = atlas_slugs & other_slugs
    union_slugs = atlas_slugs | other_slugs

    seen_union = watched & union_slugs
    seen_atlas = watched & atlas_slugs
    seen_other = watched & other_slugs
    seen_shared = watched & shared_slugs
    seen_only_atlas = watched & (atlas_slugs - other_slugs)
    seen_only_other = watched & (other_slugs - atlas_slugs)

    project_seen = []
    for slug in sorted(seen_union):
        rating = resolved_by_slug[slug]
        memberships = []
        if slug in atlas_slugs:
            memberships.append("Cinema Atlas")
        if slug in other_slugs:
            memberships.append("The Other 20th Century")
        project_seen.append(
            {
                "slug": slug,
                "name": rating["name"],
                "year": rating["year"],
                "rating": rating["rating"],
                "date": rating["date"],
                "memberships": memberships,
            }
        )

    numeric_project_ratings = [
        item["rating"] for item in project_seen if item["rating"] is not None
    ]
    numeric_all_ratings = [
        item["rating"] for item in resolved_ratings if item["rating"] is not None
    ]

    country_films: dict[str, set[str]] = {}
    for row in audit_rows:
        slug = slug_from_letterboxd_url(row.get("LetterboxdURI", ""))
        country = row.get("Country", "").strip()
        if slug and country:
            country_films.setdefault(country, set()).add(slug)

    countries_with_progress = []
    for country, slugs in country_films.items():
        seen = slugs & watched
        if seen:
            countries_with_progress.append(
                {
                    "country": country,
                    "seen": len(seen),
                    "available": len(slugs),
                    "percent": rounded_percent(len(seen), len(slugs)),
                    "films": sorted(resolved_by_slug[s]["name"] for s in seen),
                }
            )
    countries_with_progress.sort(
        key=lambda item: (-item["seen"], -item["percent"], item["country"])
    )

    top_rated = sorted(
        project_seen,
        key=lambda item: (
            -(item["rating"] if item["rating"] is not None else -1),
            item["name"],
        ),
    )[:15]
    lowest_rated = sorted(
        project_seen,
        key=lambda item: (
            item["rating"] if item["rating"] is not None else 999,
            item["name"],
        ),
    )[:15]

    result = {
        "source_file": {
            "rows": len(rating_rows),
            "resolved": sum(1 for item in resolved_ratings if item.get("slug")),
            "unresolved": sum(1 for item in resolved_ratings if not item.get("slug")),
            "unique_resolved_films": len(watched),
        },
        "lists": {
            "atlas_total": len(atlas_slugs),
            "other_20th_century_total": len(other_slugs),
            "shared_total": len(shared_slugs),
            "distinct_union_total": len(union_slugs),
        },
        "progress": {
            "distinct_project_films_seen": len(seen_union),
            "distinct_project_films_remaining": len(union_slugs - watched),
            "distinct_project_percent": rounded_percent(len(seen_union), len(union_slugs)),
            "atlas_seen": len(seen_atlas),
            "atlas_remaining": len(atlas_slugs - watched),
            "atlas_percent": rounded_percent(len(seen_atlas), len(atlas_slugs)),
            "other_seen": len(seen_other),
            "other_remaining": len(other_slugs - watched),
            "other_percent": rounded_percent(len(seen_other), len(other_slugs)),
            "shared_seen": len(seen_shared),
            "shared_remaining": len(shared_slugs - watched),
            "shared_percent": rounded_percent(len(seen_shared), len(shared_slugs)),
            "only_atlas_seen": len(seen_only_atlas),
            "only_other_seen": len(seen_only_other),
        },
        "ratings": {
            "average_all_uploaded": round(mean(numeric_all_ratings), 3)
            if numeric_all_ratings
            else None,
            "average_project_seen": round(mean(numeric_project_ratings), 3)
            if numeric_project_ratings
            else None,
            "top_rated_project_films": top_rated,
            "lowest_rated_project_films": lowest_rated,
        },
        "seen_shared_films": [
            item for item in project_seen if len(item["memberships"]) == 2
        ],
        "seen_project_films": project_seen,
        "countries_with_progress": countries_with_progress,
        "unresolved_ratings": [
            {
                "name": item["name"],
                "year": item["year"],
                "source_uri": item["source_uri"],
                "error": item["error"],
            }
            for item in resolved_ratings
            if not item.get("slug")
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(result["source_file"], ensure_ascii=False, indent=2))
    print(json.dumps(result["progress"], ensure_ascii=False, indent=2))

    if len(atlas_slugs) != 520:
        print(f"Expected 520 Atlas films, got {len(atlas_slugs)}", file=sys.stderr)
        sys.exit(2)
    if len(other_slugs) != 484:
        print(
            f"Expected 484 films in The Other 20th Century, got {len(other_slugs)}",
            file=sys.stderr,
        )
        sys.exit(3)
    if len(union_slugs) != 858:
        print(f"Expected 858 distinct project films, got {len(union_slugs)}", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
