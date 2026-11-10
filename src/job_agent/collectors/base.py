"""Shared tools for career-site connectors.

Rules applied to every request (CLAUDE.md, section 5):
- robots.txt is read once per host and respected (including Crawl-delay);
- a pause of `request_delay_seconds` (or the site's Crawl-delay, if
  longer) between two requests to the same host;
- a timeout on every request;
- a generic User-Agent without any personal data.
"""

import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from job_agent.config.models import WatchedCompany
from job_agent.offer import Offer

USER_AGENT = "job-agent/0.1 (+https://github.com/noube-marc/job_agent)"


class SourceError(Exception):
    """A source could not be read. The message is in French."""


class PoliteClient:
    def __init__(
        self,
        delay_seconds: float,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self._delay = delay_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    # --- robots.txt ---------------------------------------------------
    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                response = self._raw("GET", f"{host}/robots.txt")
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                elif response.status_code in (401, 403):
                    parser.disallow_all = True
                else:  # no robots.txt: everything is allowed
                    parser = None
            except httpx.HTTPError:
                parser = None
            self._robots[host] = parser
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        robots = self._robots_for(url)
        return robots is None or robots.can_fetch(USER_AGENT, url)

    def _delay_for(self, url: str) -> float:
        robots = self._robots_for(url)
        crawl_delay = robots.crawl_delay(USER_AGENT) if robots else None
        return max(self._delay, float(crawl_delay or 0))

    # --- requests -----------------------------------------------------
    def _raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = self._delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._client.request(method, url, **kwargs)
        finally:
            self._last_request[host] = self._clock()
            self.requests_made += 1

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not self.allowed(url):
            raise SourceError(f"accès interdit par robots.txt : {url}")
        delay = self._delay_for(url)
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None and delay > self._delay:
            wait = delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            response = self._raw(method, url, **kwargs)
        except httpx.TimeoutException:
            raise SourceError(f"délai dépassé ({urlsplit(url).netloc})") from None
        except httpx.HTTPError as error:
            raise SourceError(f"site injoignable ({urlsplit(url).netloc}, {type(error).__name__})") from None
        if response.status_code >= 400:
            raise SourceError(f"réponse HTTP {response.status_code} ({urlsplit(url).netloc})")
        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


class Connector(Protocol):
    """Reads the public offers of one company on one recruiting tool."""

    def __call__(self, company: WatchedCompany, client: PoliteClient) -> list[Offer]: ...
