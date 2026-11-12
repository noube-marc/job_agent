"""Shared tools for career-site connectors.

Rules applied to every request (CLAUDE.md, section 5):
- robots.txt is read once per host and respected (including Crawl-delay);
- a pause of `request_delay_seconds` (or the site's Crawl-delay, if
  longer) between two requests to the same host;
- a timeout on every request;
- two more tries when a site drops the connection, times out or answers
  "too many requests" / "unavailable" (429, 5xx), after 5 then 20 s (or the
  pause the site asks in Retry-After, up to 60 s);
- a generic User-Agent without any personal data.
"""

import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from job_agent.collectors.robots import RobotRules
from job_agent.config.models import AppConfig, WatchedCompany
from job_agent.offer import Offer

# A busy or flaky site gets two more tries, after these pauses (seconds).
RETRIES = 2
RETRY_WAITS = (5.0, 20.0)
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60.0


def _retry_after(response: httpx.Response) -> float | None:
    """The pause asked by the site (Retry-After, in seconds), within reason."""
    value = response.headers.get("Retry-After", "")
    return min(float(value), MAX_RETRY_AFTER) if value.strip().isdigit() else None


USER_AGENT = "job-agent/0.1 (+https://github.com/noube-marc/job_agent)"


_BLOCK_TAGS = re.compile(r"<\s*(br|/p|/div|/h\d|/tr|/ul|/ol)\b[^>]*>", re.IGNORECASE)
_LIST_ITEM = re.compile(r"<\s*li\b[^>]*>", re.IGNORECASE)
_INLINE_TAGS = re.compile(r"</?\s*(b|i|u|em|strong|span|a|font|sup|sub|small)\b[^>]*>", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


def html_to_text(fragment: str) -> str:
    """Readable plain text from an HTML fragment (line breaks kept)."""
    text = _LIST_ITEM.sub("\n- ", _BLOCK_TAGS.sub("\n", _INLINE_TAGS.sub("", fragment)))
    text = html.unescape(_TAGS.sub(" ", text)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = re.sub(r"\n{2,}(?=- )", "\n", "\n".join(lines))  # no blank line before list items
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class SourceError(Exception):
    """A source could not be read. The message is in French."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        self._robots: dict[str, RobotRules | None] = {}
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    # --- robots.txt ---------------------------------------------------
    def _robots_for(self, url: str) -> RobotRules | None:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser: RobotRules | None = None
            try:
                response = self._raw("GET", f"{host}/robots.txt")
                # RFC 9309: a 4xx answer means "no rules" (everything is
                # allowed); a 5xx answer means "assume everything is forbidden".
                if response.status_code == 200:
                    parser = RobotRules(response.text)
                elif response.status_code >= 500:
                    parser = RobotRules(disallow_all=True)
                else:
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
        for attempt in range(RETRIES + 1):
            last_try = attempt == RETRIES
            try:
                response = self._raw(method, url, **kwargs)
            except httpx.TimeoutException:
                if not last_try:
                    self._sleep(RETRY_WAITS[attempt])
                    continue
                raise SourceError(f"délai dépassé ({host})") from None
            except httpx.HTTPError as error:
                if not last_try:
                    self._sleep(RETRY_WAITS[attempt])
                    continue
                raise SourceError(f"site injoignable ({host}, {type(error).__name__})") from None
            if response.status_code in RETRY_STATUSES and not last_try:
                self._sleep(_retry_after(response) or RETRY_WAITS[attempt])
                continue
            if response.status_code >= 400:
                raise SourceError(f"réponse HTTP {response.status_code} ({host})", response.status_code)
            return response
        raise SourceError(f"site injoignable ({host})")  # not reached

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


@dataclass
class CollectContext:
    """What a connector may use during a run."""

    client: PoliteClient
    config: AppConfig
    now: datetime
    # is_known(source_name, source_offer_id): offer already stored, so a
    # connector can skip fetching its details again.
    is_known: Callable[[str, str], bool] = lambda source_name, offer_id: False
    # Offers opened once and found outside the configured country: never
    # opened again (is_skipped), and recorded by mark_skipped.
    is_skipped: Callable[[str, str], bool] = lambda source_name, offer_id: False
    mark_skipped: Callable[[str, str], None] = lambda source_name, offer_id: None


class Connector(Protocol):
    """Reads the public offers of one company on one recruiting tool."""

    def __call__(self, company: WatchedCompany, context: CollectContext) -> list[Offer]: ...
