"""HTTP infrastructure with retries, jitter, rate limiting, and proxy fallback."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

import requests

from letterboxd_scraper.config import HttpConfig
from letterboxd_scraper.exceptions import FetchError

_RETRYABLE_STATUS_CODES = {403, 408, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Small transport-neutral response used by scraper services."""

    url: str
    status_code: int
    text: str
    source: str


class RateLimiter:
    """Coordinate a minimum interval between requests across worker threads."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = max(0.0, min_interval_seconds)
        self._lock = threading.Lock()
        self._next_allowed_at = 0.0

    def wait(self) -> None:
        if self._min_interval == 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_allowed_at - now)
            self._next_allowed_at = max(now, self._next_allowed_at) + self._min_interval
        if sleep_for:
            time.sleep(sleep_for)


class HttpClient:
    """Thread-safe HTTP client built on one requests session per worker thread."""

    def __init__(self, config: HttpConfig) -> None:
        self._config = config
        self._local = threading.local()
        self._rate_limiter = RateLimiter(config.min_request_interval_seconds)

    def get(self, url: str, *, allow_404: bool = False) -> HttpResponse:
        """Fetch a URL directly and retry transient or anti-bot responses."""
        last_error: Exception | None = None
        for attempt in range(self._config.max_attempts):
            self._rate_limiter.wait()
            try:
                response = self._session().get(url, timeout=self._config.timeout_seconds)
                if response.status_code == 200 or (allow_404 and response.status_code == 404):
                    return HttpResponse(
                        url=response.url,
                        status_code=response.status_code,
                        text=response.text,
                        source="direct",
                    )
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                last_error = FetchError(f"HTTP {response.status_code} for {url}")
            except requests.RequestException as exc:
                last_error = exc

            if attempt + 1 < self._config.max_attempts:
                time.sleep(self._backoff(attempt))

        raise FetchError(
            f"Could not fetch {url} after {self._config.max_attempts} attempts"
        ) from last_error

    def get_with_jina_fallback(self, url: str, *, allow_404: bool = False) -> HttpResponse:
        """Try Letterboxd directly, then use Jina Reader when configured."""
        try:
            direct = self.get(url, allow_404=allow_404)
            if direct.status_code == 404 or direct.text.strip():
                return direct
        except FetchError:
            if not self._config.use_jina_fallback:
                raise

        if not self._config.use_jina_fallback:
            raise FetchError(f"Direct response for {url} was empty and fallback is disabled")
        return self.get_jina(url)

    def get_jina(self, url: str) -> HttpResponse:
        """Fetch a public page through Jina Reader using the HTTPS target URL.

        The target must remain HTTPS. Using an HTTP target can return a security
        verification page instead of the requested Letterboxd content.
        """
        target = url if url.startswith(("https://", "http://")) else f"https://{url}"
        if target.startswith("http://"):
            target = "https://" + target.removeprefix("http://")
        jina_url = f"https://r.jina.ai/{target}"

        response = self.get(jina_url)
        body = response.text
        if "Performing security verification" in body or "Just a moment..." in body:
            raise FetchError(f"Jina returned a security verification page for {url}")
        return HttpResponse(
            url=url,
            status_code=response.status_code,
            text=body,
            source="jina",
        )

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": self._config.user_agent,
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            self._local.session = session
        return session

    def _backoff(self, attempt: int) -> float:
        base = self._config.backoff_base_seconds * (2**attempt)
        jitter = random.uniform(0.25, 1.25)
        return float(min(self._config.max_backoff_seconds, base + jitter))
