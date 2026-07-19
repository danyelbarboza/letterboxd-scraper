from __future__ import annotations

from pathlib import Path

import requests

BASE = "https://letterboxd.com/imthelizardking/list/all-the-movies-10k-views-4/"
URLS = {
    "brazil": BASE + "country/brazil/language/portuguese/",
    "afghanistan": BASE + "country/afghanistan/language/persian-farsi/",
    "antarctica": BASE + "country/antarctica/",
    "wrong_afghanistan_detail": BASE
    + "country/afghanistan/language/persian-farsi/detail/page/1/",
}


def main() -> None:
    output = Path("output/debug-filtered-pages")
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    for name, url in URLS.items():
        direct = session.get(url, timeout=45)
        (output / f"{name}.direct.status.txt").write_text(
            f"status={direct.status_code}\nurl={direct.url}\n", encoding="utf-8"
        )
        (output / f"{name}.direct.html").write_text(direct.text, encoding="utf-8")

        jina_url = f"https://r.jina.ai/{url}"
        jina = session.get(jina_url, timeout=90)
        (output / f"{name}.jina.status.txt").write_text(
            f"status={jina.status_code}\nurl={jina.url}\n", encoding="utf-8"
        )
        (output / f"{name}.jina.md").write_text(jina.text, encoding="utf-8")
        print(name, direct.status_code, len(direct.text), jina.status_code, len(jina.text))


if __name__ == "__main__":
    main()
