from __future__ import annotations

import csv
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

import pycountry
from babel import Locale
from babel.core import get_global
from bs4 import BeautifulSoup
from pycountry_convert import country_alpha2_to_continent_code

from letterboxd_scraper.cache import FilmCache
from letterboxd_scraper.config import CacheConfig, HttpConfig
from letterboxd_scraper.film_resolver import FilmResolver
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.list_scraper import ListScraper
from letterboxd_scraper.models import FilmDetails

BASE = "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/"
INDEX = "https://letterboxd.com/countries/"
OUT = Path("output/top10-by-country")
TOP_N = 10
CONTINENTS = {"AF":"Africa","AS":"Asia","EU":"Europe","NA":"North America","OC":"Oceania","SA":"South America","AN":"Antarctica"}
COUNTRY_CODES = {
    "Bolivia":"BO","Brunei Darussalam":"BN","Cabo Verde":"CV","Congo":"CG","Côte d’Ivoire":"CI",
    "Czechia":"CZ","Democratic Republic of Congo":"CD","Eswatini":"SZ","Federated States of Micronesia":"FM",
    "Hong Kong":"HK","Iran":"IR","Laos":"LA","Macao":"MO","Moldova":"MD","North Korea":"KP",
    "State of Palestine":"PS","Russia":"RU","South Korea":"KR","Syria":"SY","Taiwan":"TW","Tanzania":"TZ",
    "Timor-Leste":"TL","Türkiye":"TR","Venezuela":"VE","Vietnam":"VN",
}
HISTORICAL = {
    "Czechoslovakia":(("cs","sk"),"EU"),
    "East Germany":(("de",),"EU"),
    "Serbia and Montenegro":(("sr","me"),"EU"),
    "Soviet Union":(("ru","uk","be","ka","hy","az","kk","uz","lt","lv","et"),"EU"),
    "USSR":(("ru","uk","be","ka","hy","az","kk","uz","lt","lv","et"),"EU"),
    "Yugoslavia":(("sh","sr","hr","sl","bs","mk"),"EU"),
}
LANG_NAMES = {
    "bn":"Bengali, Bangla","dv":"Divehi, Dhivehi, Maldivian","el":"Greek (modern)","fa":"Persian (Farsi)",
    "fil":"Tagalog","gd":"Scottish Gaelic, Gaelic","he":"Hebrew (modern)","ht":"Haitian, Haitian Creole",
    "kl":"Kalaallisut, Greenlandic","mi":"Māori","nb":"Norwegian Bokmål","nn":"Norwegian Nynorsk",
    "ny":"Chichewa, Chewa, Nyanja","or":"Oriya","pa":"Eastern Punjabi, Eastern Panjabi","ps":"Pashto, Pushto",
    "si":"Sinhalese, Sinhala","st":"Southern Sotho","yue":"Cantonese","zh":"Chinese","zh_Hans":"Chinese","zh_Hant":"Chinese",
}
ADDITIONS = {"Hong Kong":("yue",),"Macao":("yue",)}
SCRIPT_SUFFIX = re.compile(r"_[A-Z][a-z]{3}$")


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def parse_count(text: str):
    match = re.search(r"([0-9][0-9,]*)\s*$", text)
    return int(match.group(1).replace(",", "")) if match else None


def filter_options(http: HttpClient):
    response = http.get_with_jina_fallback(INDEX)
    countries, languages = {}, {}
    if response.source == "jina":
        pattern = re.compile(r"\[([^\]]+)\]\((?:https?://letterboxd\.com)?/films/(country|language)/([^/]+)/?\)")
        for name, kind, slug in pattern.findall(response.text):
            (countries if kind == "country" else languages)[slug] = {"name":name.strip(),"slug":slug,"count":None}
    else:
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a[href]"):
            match = re.search(r"/films/(country|language)/([^/]+)/", str(anchor.get("href") or ""))
            if not match:
                continue
            kind, slug = match.groups()
            text = anchor.get_text(" ", strip=True)
            name = re.sub(r"\s+[0-9][0-9,]*\s*$", "", text).strip()
            if name:
                (countries if kind == "country" else languages)[slug] = {"name":name,"slug":slug,"count":parse_count(text)}
    if not countries or not languages:
        raise RuntimeError(f"Could not parse filters: {len(countries)} countries, {len(languages)} languages")
    return countries, languages


def alpha2(name: str):
    if name in COUNTRY_CODES:
        return COUNTRY_CODES[name]
    try:
        return pycountry.countries.lookup(name).alpha_2
    except LookupError:
        wanted = norm(name)
        for country in pycountry.countries:
            if wanted in {norm(country.name), norm(getattr(country,"official_name","")), norm(getattr(country,"common_name",""))}:
                return country.alpha_2
    return None


def language_codes(country: str, code: str | None):
    if country in HISTORICAL:
        return HISTORICAL[country][0]
    if not code:
        return ()
    data = get_global("territory_languages").get(code, {})
    statuses = {"official","de_facto_official","official_regional"}
    chosen = [(key,value) for key,value in data.items() if value.get("official_status") in statuses]
    if not chosen and data:
        chosen = [max(data.items(), key=lambda item:item[1].get("population_percent",0))]
    result = []
    for key,value in sorted(chosen, key=lambda item:(-float(item[1].get("population_percent",0)), item[0])):
        key = key if key in LANG_NAMES else SCRIPT_SUFFIX.sub("", key)
        if key not in result:
            result.append(key)
    for key in ADDITIONS.get(country, ()):
        if key not in result:
            result.append(key)
    return tuple(result)


def language_name(code: str):
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    base = code.split("_")[0]
    try:
        return Locale("en").languages.get(base) or base
    except Exception:
        item = pycountry.languages.get(alpha_2=base)
        return item.name if item else base


def language_lookup(options):
    lookup = {}
    for option in options.values():
        for key in {norm(option["name"]), norm(option["name"].split(",")[0]), norm(option["slug"].replace("-"," "))}:
            if key:
                lookup.setdefault(key, option)
    return lookup


def match_language(code, options, lookup):
    wanted = language_name(code)
    for key in (norm(wanted), norm(wanted.split(",")[0]), norm(code.replace("_"," "))):
        if key in lookup:
            return lookup[key]
    target = norm(wanted)
    candidates = [option for option in options.values() if len(target) >= 4 and (target in norm(option["name"]) or norm(option["name"]) in target)]
    return candidates[0] if len(candidates) == 1 else None


def continent(country, code):
    if country in HISTORICAL:
        return CONTINENTS[HISTORICAL[country][1]]
    if not code:
        return "Unmapped"
    try:
        return CONTINENTS[country_alpha2_to_continent_code(code)]
    except Exception:
        return "Unmapped"


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def import_rows(rows):
    unique = {}
    for row in rows:
        uri = str(row["LetterboxdURI"])
        unique.setdefault(uri, {"Title":row["Title"],"Year":row["Year"],"LetterboxdURI":uri})
    return sorted(unique.values(), key=lambda row:(str(row["Title"]).casefold(), row["Year"] or 0, row["LetterboxdURI"]))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    config = HttpConfig(timeout_seconds=30,max_attempts=1,backoff_base_seconds=.5,max_backoff_seconds=2,concurrency=10,min_request_interval_seconds=.05,use_jina_fallback=True)
    http = HttpClient(config)
    scraper = ListScraper(http, filters=(), max_pages=1)
    resolver = FilmResolver(http, FilmCache(CacheConfig(enabled=True,directory=Path(".cache/letterboxd-country"),ttl_hours=168)), concurrency=10)
    countries, languages = filter_options(http)
    lookup = language_lookup(languages)
    print(f"Discovered {len(countries)} countries and {len(languages)} languages", flush=True)
    audit, country_summary, country_languages, unresolved = [], [], [], []
    by_continent = defaultdict(list)
    unmapped, unmatched = [], []
    country_list = sorted(countries.values(), key=lambda item:item["name"].casefold())
    for idx, country_opt in enumerate(country_list, 1):
        country = country_opt["name"]
        code = alpha2(country)
        cont = continent(country, code)
        if not code and country not in HISTORICAL:
            unmapped.append({"Country":country,"CountrySlug":country_opt["slug"],"LetterboxdCountryCount":country_opt["count"],"Reason":"No ISO/CLDR country mapping"})
        matched, missing_codes, seen = [], [], set()
        for lang_code in language_codes(country, code):
            option = match_language(lang_code, languages, lookup)
            if not option:
                missing_codes.append(lang_code)
                unmatched.append({"Country":country,"CountrySlug":country_opt["slug"],"LanguageCode":lang_code,"ExpectedLanguageName":language_name(lang_code)})
            elif option["slug"] not in seen:
                matched.append((lang_code, option)); seen.add(option["slug"])
        runs = []
        for lang_code, option in matched:
            source = urljoin(BASE, f"country/{country_opt['slug']}/language/{option['slug']}/")
            try:
                result = scraper.scrape(source)
                refs = list(result.films.values())[:TOP_N]
                runs.append({"code":lang_code,"option":option,"source":source,"refs":refs,"sources":result.source_counts,"error":""})
            except Exception as exc:
                runs.append({"code":lang_code,"option":option,"source":source,"refs":[],"sources":{},"error":repr(exc)})
        candidate_map = {}
        for run in runs:
            for position, ref in enumerate(run["refs"], 1):
                info = candidate_map.setdefault(ref.uri, {"ref":ref,"position":position,"languages":set(),"codes":set(),"sources":set()})
                info["ref"] = info["ref"].merge(ref)
                info["position"] = min(info["position"], position)
                info["languages"].add(run["option"]["name"]); info["codes"].add(run["code"]); info["sources"].add(run["source"])
        refs = {uri:info["ref"] for uri,info in candidate_map.items()}
        resolved, failed = resolver.resolve_many(refs) if refs else ([], [])
        unresolved.extend({"Country":country,"Continent":cont,"Title":item.title,"Year":item.year,"LetterboxdURI":item.uri,"Error":item.error} for item in failed)
        details_by_uri = {item.uri:item for item in resolved + failed}
        ranked = []
        for uri, info in candidate_map.items():
            item = details_by_uri.get(uri) or FilmDetails(uri=uri,title=info["ref"].title,year=info["ref"].year,average_rating=None,error="No resolver record")
            ranked.append((item, info))
        ranked.sort(key=lambda pair:(pair[0].average_rating is None, -(pair[0].average_rating or -1), pair[1]["position"], pair[0].title.casefold(), pair[0].uri))
        selected = ranked[:TOP_N]
        for rank, (item, info) in enumerate(selected, 1):
            row = {"Country":country,"CountryCode":code or "","CountrySlug":country_opt["slug"],"Continent":cont,"Rank":rank,
                   "Title":item.title or info["ref"].title,"Year":item.year or info["ref"].year,"LetterboxdURI":item.uri,"AverageRating":item.average_rating,
                   "MatchedLanguages":"; ".join(sorted(info["languages"])),"LanguageCodes":"; ".join(sorted(info["codes"])),
                   "BestPositionInLanguageFilter":info["position"],"SourceURLs":"; ".join(sorted(info["sources"])),
                   "RatingSource":item.rating_source,"MetadataSource":item.metadata_source,"Error":item.error}
            audit.append(row); by_continent[cont].append(row)
        selected_count = len(selected)
        country_summary.append({"Country":country,"CountryCode":code or "","CountrySlug":country_opt["slug"],"Continent":cont,
            "LetterboxdCountryCount":country_opt["count"],"LanguageCodes":"; ".join(x[0] for x in matched),"Languages":"; ".join(x[1]["name"] for x in matched),
            "LanguageCount":len(matched),"CandidateCount":len(candidate_map),"FilmsSelected":selected_count,
            "FilmsWithImportMetadata":sum(1 for item,_ in selected if item.title and item.year),"FilmsWithResolvedRating":sum(1 for item,_ in selected if item.average_rating is not None),
            "Status":"ok" if selected_count == TOP_N else ("partial" if selected_count else "empty"),"Errors":" | ".join(run["error"] for run in runs if run["error"])})
        selected_uris = {item.uri for item,_ in selected}
        for run in runs:
            run_uris = {ref.uri for ref in run["refs"]}
            country_languages.append({"Country":country,"CountryCode":code or "","CountrySlug":country_opt["slug"],"Continent":cont,
                "LanguageCode":run["code"],"Language":run["option"]["name"],"LanguageSlug":run["option"]["slug"],"SourceURL":run["source"],
                "CandidatesRead":len(run["refs"]),"SelectedFromThisLanguage":len(selected_uris & run_uris),"FilmsSelectedForCountry":selected_count,
                "TargetPerCountry":TOP_N,"SourceCounts":json.dumps(run["sources"],sort_keys=True),"Error":run["error"]})
        for lang_code in missing_codes:
            country_languages.append({"Country":country,"CountryCode":code or "","CountrySlug":country_opt["slug"],"Continent":cont,
                "LanguageCode":lang_code,"Language":language_name(lang_code),"LanguageSlug":"","SourceURL":"","CandidatesRead":0,
                "SelectedFromThisLanguage":0,"FilmsSelectedForCountry":selected_count,"TargetPerCountry":TOP_N,"SourceCounts":"{}","Error":"No matching Letterboxd language filter"})
        if not runs and not missing_codes:
            country_languages.append({"Country":country,"CountryCode":code or "","CountrySlug":country_opt["slug"],"Continent":cont,
                "LanguageCode":"","Language":"","LanguageSlug":"","SourceURL":"","CandidatesRead":0,"SelectedFromThisLanguage":0,
                "FilmsSelectedForCountry":selected_count,"TargetPerCountry":TOP_N,"SourceCounts":"{}","Error":"No matched Letterboxd language filter"})
        print(f"[{idx:03d}/{len(country_list):03d}] {country}: {selected_count}/{TOP_N}, {len(matched)} language(s), {len(candidate_map)} candidates", flush=True)
        if idx % 25 == 0:
            write_csv(OUT/"checkpoint_country_summary.csv", list(country_summary[0].keys()), country_summary)
    audit_fields = ["Country","CountryCode","CountrySlug","Continent","Rank","Title","Year","LetterboxdURI","AverageRating","MatchedLanguages","LanguageCodes","BestPositionInLanguageFilter","SourceURLs","RatingSource","MetadataSource","Error"]
    write_csv(OUT/"top_10_by_country_audit.csv", audit_fields, audit)
    main_import = import_rows(audit)
    write_csv(OUT/"top_10_by_country_letterboxd_import.csv", ["Title","Year","LetterboxdURI"], main_import)
    for cont, rows in sorted(by_continent.items()):
        write_csv(OUT/"continents"/f"{norm(cont).replace(chr(32), chr(95)) or 'unmapped'}.csv", ["Title","Year","LetterboxdURI"], import_rows(rows))
    write_csv(OUT/"countries_and_languages.csv", ["Country","CountryCode","CountrySlug","Continent","LanguageCode","Language","LanguageSlug","SourceURL","CandidatesRead","SelectedFromThisLanguage","FilmsSelectedForCountry","TargetPerCountry","SourceCounts","Error"], country_languages)
    write_csv(OUT/"country_summary.csv", ["Country","CountryCode","CountrySlug","Continent","LetterboxdCountryCount","LanguageCodes","Languages","LanguageCount","CandidateCount","FilmsSelected","FilmsWithImportMetadata","FilmsWithResolvedRating","Status","Errors"], country_summary)
    write_csv(OUT/"unresolved_films.csv", ["Country","Continent","Title","Year","LetterboxdURI","Error"], unresolved)
    write_csv(OUT/"unmapped_countries.csv", ["Country","CountrySlug","LetterboxdCountryCount","Reason"], unmapped)
    write_csv(OUT/"unmatched_languages.csv", ["Country","CountrySlug","LanguageCode","ExpectedLanguageName"], unmatched)
    summary = {"generated_at_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"source_list":BASE,"target_per_country":TOP_N,
        "countries_discovered":len(country_list),"countries_with_10":sum(row["FilmsSelected"]==TOP_N for row in country_summary),
        "countries_partial":sum(0<row["FilmsSelected"]<TOP_N for row in country_summary),"countries_empty":sum(row["FilmsSelected"]==0 for row in country_summary),
        "audit_rows":len(audit),"unique_import_films":len(main_import),"unmapped_countries":len(unmapped),"unmatched_country_language_codes":len(unmatched),
        "unresolved_records":len(unresolved),"methodology":{"country_universe":"All country/territory/historical-country filters on Letterboxd Countries page",
        "languages":"CLDR official, de-facto official, and official-regional languages matched to Letterboxd filters",
        "selection":"Top 10 from each language-filter page, merged per country, ranked by current Letterboxd average rating",
        "deduplication":"Import CSVs unique by canonical URI; audit retains every country assignment"}}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(summary,indent=2,ensure_ascii=False), flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())
