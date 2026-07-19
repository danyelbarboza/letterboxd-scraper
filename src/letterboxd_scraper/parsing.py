"""Pure parsing functions for Letterboxd list pages and film pages."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from letterboxd_scraper.models import FilmDetails, FilmRef

LETTERBOXD_BASE_URL = "https://letterboxd.com"
_FILM_URI_PATTERN = re.compile(r"https://letterboxd\.com/film/[^/?#)\s]+/")
_YEAR_PATTERN = re.compile(r"\b(?:18|19|20|21)\d{2}\b")
_RATING_PATTERN = re.compile(
    r"(?<!\d)([0-5](?:\.\d+)?)\s*(?:avg rating|out of 5)(?!\d)",
    flags=re.IGNORECASE,
)


def canonicalize_film_uri(raw_url: str) -> str | None:
    """Return a canonical Letterboxd film URL or None for unrelated links."""
    if not raw_url:
        return None
    absolute = urljoin(LETTERBOXD_BASE_URL, raw_url)
    match = _FILM_URI_PATTERN.search(absolute)
    return match.group(0) if match else None


def parse_list_html(html: str) -> dict[str, FilmRef]:
    """Extract canonical films from current and legacy Letterboxd list markup."""
    soup = BeautifulSoup(html, "html.parser")
    films: dict[str, FilmRef] = {}
    selectors = (
        "div.film-poster[data-target-link], "
        "div.react-component[data-target-link], "
        "li.poster-container div[data-target-link], "
        "a.frame[href*='/film/']"
    )
    for node in soup.select(selectors):
        raw_link = node.get("data-target-link") or node.get("href") or ""
        uri = canonicalize_film_uri(str(raw_link))
        if not uri:
            continue
        film = FilmRef(uri=uri, title=_extract_node_title(node), year=_extract_node_year(node))
        films[uri] = films[uri].merge(film) if uri in films else film
    return films


def parse_list_markdown(markdown: str) -> dict[str, FilmRef]:
    """Extract film links from Jina Reader markdown.

    Jina output may contain absolute links or relative ``/film/.../`` links.
    Metadata is intentionally left partial and resolved from each film page.
    """
    films: dict[str, FilmRef] = {}
    patterns = (
        re.compile(r"https://letterboxd\.com/film/[^/?#)\s]+/"),
        re.compile(r"(?<![A-Za-z0-9])(/film/[^/?#)\s]+/)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(markdown):
            raw = match.group(1) if match.lastindex else match.group(0)
            uri = canonicalize_film_uri(raw)
            if uri:
                films.setdefault(uri, FilmRef(uri=uri))
    return films


def parse_film_html(html: str, fallback: FilmRef) -> FilmDetails:
    """Resolve title, year, and average rating from a Letterboxd film page."""
    soup = BeautifulSoup(html, "html.parser")
    rating, rating_source = _extract_rating(soup, html)
    title = _extract_title(soup) or fallback.title
    year = _extract_year(soup) or fallback.year
    return FilmDetails(
        uri=fallback.uri,
        title=title,
        year=year,
        average_rating=rating,
        rating_source=rating_source,
        metadata_source="film-html",
    )


def parse_film_markdown(markdown: str, fallback: FilmRef) -> FilmDetails:
    """Best-effort film parsing for Jina Reader markdown responses."""
    rating_match = _RATING_PATTERN.search(markdown)
    rating = float(rating_match.group(1)) if rating_match else None

    title = fallback.title
    title_match = re.search(r"^Title:\s*(.+)$", markdown, flags=re.MULTILINE)
    if title_match:
        title = re.sub(r"\s*•\s*Letterboxd\s*$", "", title_match.group(1)).strip()

    year = fallback.year
    if year is None:
        year_match = _YEAR_PATTERN.search(markdown[:5000])
        year = int(year_match.group(0)) if year_match else None

    return FilmDetails(
        uri=fallback.uri,
        title=title,
        year=year,
        average_rating=rating,
        rating_source="markdown-regex" if rating is not None else "",
        metadata_source="jina-markdown",
    )


def _extract_node_title(node: Tag) -> str:
    title = str(
        node.get("data-item-name")
        or node.get("data-item-full-display-name")
        or node.get("data-film-name")
        or node.get("data-film-title")
        or ""
    ).strip()
    image = node.find("img")
    if not title and image:
        title = str(image.get("alt") or "").strip()
    return re.sub(r"\s+\((?:18|19|20|21)\d{2}\)\s*$", "", title).strip()


def _extract_node_year(node: Tag) -> int | None:
    values: Iterable[object] = (
        node.get("data-item-year"),
        node.get("data-film-release-year"),
        node.get("data-film-year"),
    )
    for value in values:
        match = _YEAR_PATTERN.search(str(value or ""))
        if match:
            return int(match.group(0))
    return None


def _extract_rating(soup: BeautifulSoup, html: str) -> tuple[float | None, str]:
    twitter_data = soup.select_one("meta[name='twitter:data2']")
    if twitter_data:
        match = _RATING_PATTERN.search(str(twitter_data.get("content") or ""))
        if match:
            return float(match.group(1)), "twitter:data2"

    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for item in objects:
            if not isinstance(item, dict):
                continue
            aggregate = item.get("aggregateRating")
            if not isinstance(aggregate, dict):
                continue
            try:
                return float(aggregate["ratingValue"]), "json-ld"
            except (KeyError, TypeError, ValueError):
                continue

    match = _RATING_PATTERN.search(html)
    if match:
        return float(match.group(1)), "page-regex"
    return None, ""


def _extract_title(soup: BeautifulSoup) -> str:
    og_title = soup.select_one("meta[property='og:title']")
    if not og_title:
        return ""
    title = str(og_title.get("content") or "").strip()
    title = re.sub(r"\s+•\s+Letterboxd\s*$", "", title).strip()
    return re.sub(r"\s+\((?:18|19|20|21)\d{2}\)\s*$", "", title).strip()


def _extract_year(soup: BeautifulSoup) -> int | None:
    year_node = soup.select_one("small.number a, small.number")
    if year_node:
        match = _YEAR_PATTERN.search(year_node.get_text(" ", strip=True))
        if match:
            return int(match.group(0))

    og_title = soup.select_one("meta[property='og:title']")
    if og_title:
        match = _YEAR_PATTERN.search(str(og_title.get("content") or ""))
        if match:
            return int(match.group(0))

    description = soup.select_one("meta[property='og:description']")
    if description:
        match = _YEAR_PATTERN.search(str(description.get("content") or ""))
        if match:
            return int(match.group(0))
    return None
