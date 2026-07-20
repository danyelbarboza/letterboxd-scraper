from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://letterboxd.com/danyel/list/the-other-20th-century/"
OUT = Path("output/other_20th_century_full.json")
USER_AGENT = "Mozilla/5.0 (compatible; LetterboxdListToolkit/1.0)"


def scrape_slugs() -> list[str]:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    slugs: dict[str, None] = {}
    for page in range(1, 21):
        url = LIST_URL if page == 1 else f"{LIST_URL}page/{page}/"
        response = session.get(url, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup.select("[data-film-slug], [data-target-link], a[href*='/film/']"):
            slug = node.get("data-film-slug")
            href = node.get("data-target-link") or node.get("href") or ""
            if not slug:
                match = re.search(r"/film/([^/]+)/", href)
                slug = match.group(1) if match else None
            if slug:
                slugs.setdefault(slug, None)
        next_link = soup.select_one("a.next") or soup.find("a", string=re.compile("Next", re.I))
        if not next_link:
            break
    return list(slugs)


def parse_year(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\b(18|19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


def fetch_metadata(slug: str) -> dict[str, Any]:
    url = f"https://letterboxd.com/film/{slug}/"
    response = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    title_node = soup.select_one("h1.headline-1, h1.filmtitle, meta[property='og:title']")
    if title_node and title_node.name == "meta":
        title = title_node.get("content", "").strip()
    else:
        title = title_node.get_text(" ", strip=True) if title_node else slug

    year_node = soup.select_one("small.number a, .releaseyear a, a[href*='/films/year/']")
    year = parse_year(year_node.get_text(" ", strip=True) if year_node else None)
    if year is None:
        year = parse_year(soup.title.get_text(" ", strip=True) if soup.title else None)

    title = re.sub(r"\s*\(\d{4}\)\s*[-–—|].*$", "", title).strip()
    title = re.sub(r"\s*[-–—|]\s*Letterboxd.*$", "", title, flags=re.I).strip()
    return {"slug": slug, "title": title, "year": year, "url": url}


def main() -> None:
    slugs = scrape_slugs()
    films: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_metadata, slug): slug for slug in slugs}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                films.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append({"slug": slug, "error": f"{type(exc).__name__}: {exc}"})

    films.sort(key=lambda item: item["slug"])
    result = {"count": len(slugs), "resolved": len(films), "errors": errors, "films": films}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "films"}, ensure_ascii=False, indent=2))
    if len(slugs) != 484:
        raise SystemExit(f"Expected 484 list films, got {len(slugs)}")
    if errors:
        raise SystemExit(f"Failed to enrich {len(errors)} films")


if __name__ == "__main__":
    main()
