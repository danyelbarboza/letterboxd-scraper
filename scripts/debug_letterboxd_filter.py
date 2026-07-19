from pathlib import Path

from letterboxd_scraper.config import HttpConfig
from letterboxd_scraper.http import HttpClient
from letterboxd_scraper.list_scraper import build_list_page_urls

OUTPUT = Path("output/filter-debug")
OUTPUT.mkdir(parents=True, exist_ok=True)

http = HttpClient(
    HttpConfig(
        timeout_seconds=30,
        max_attempts=1,
        concurrency=2,
        min_request_interval_seconds=0.1,
        use_jina_fallback=True,
    )
)

urls = {
    "albania": "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/country/albania/language/albanian/",
    "egypt": "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/country/egypt/language/arabic/",
    "brazil": "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/country/brazil/language/portuguese/",
}

for name, source in urls.items():
    page_urls = build_list_page_urls(source, 1, ())
    (OUTPUT / f"{name}-urls.txt").write_text("\n".join(page_urls), encoding="utf-8")
    for index, url in enumerate(page_urls):
        try:
            response = http.get_jina(url)
            body = response.text
        except Exception as exc:
            body = repr(exc)
        (OUTPUT / f"{name}-{index}.md").write_text(body, encoding="utf-8")

print(f"Captured {len(urls) * 2} filtered page responses")
