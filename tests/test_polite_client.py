"""The HTTP client respects robots.txt, delays and identifies itself generically."""

import httpx
import pytest

from job_agent.collectors.base import USER_AGENT, PoliteClient, SourceError

ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 5\n"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 2))
        self.now += seconds


def _client(handler, clock: FakeClock, delay: float = 2) -> PoliteClient:
    return PoliteClient(delay, 10, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock.time)


def test_robots_delay_and_user_agent() -> None:
    seen_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_agents.append(request.headers["User-Agent"])
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        return httpx.Response(200, json={"ok": True})

    clock = FakeClock()
    client = _client(handler, clock)
    client.get("https://site.example/jobs")
    client.get("https://site.example/jobs?page=2")
    with pytest.raises(SourceError, match="robots.txt"):
        client.get("https://site.example/private/x")

    assert set(seen_agents) == {USER_AGENT}
    assert "@" not in USER_AGENT  # no personal e-mail
    # Crawl-delay (5 s) is longer than the configured delay (2 s): it wins.
    assert clock.slept and max(clock.slept) == 5


def test_http_errors_become_source_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503)

    client = _client(handler, FakeClock())
    with pytest.raises(SourceError, match="HTTP 503"):
        client.get("https://down.example/jobs")


def test_robots_server_error_forbids_everything() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if request.url.path == "/robots.txt" else 200)

    client = _client(handler, FakeClock())
    with pytest.raises(SourceError, match="robots.txt"):
        client.get("https://flaky.example/jobs")


def test_flaky_site_gets_two_more_tries() -> None:
    calls: list[str] = []
    pauses: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("connexion coupée")
        if len(calls) == 2:
            return httpx.Response(503, headers={"Retry-After": "12"})
        return httpx.Response(200, text="ok")

    client = PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=pauses.append)
    assert client.get("https://site.example/offres").text == "ok"
    assert len(calls) == 3 and 5.0 in pauses and 12.0 in pauses  # Retry-After respected


def test_site_still_down_after_retries_is_an_error() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        return httpx.Response(502)

    client = PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(SourceError, match="502"):
        client.get("https://site.example/offres")
    assert len(calls) == 3


def test_page_not_found_is_not_retried() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        return httpx.Response(404)

    client = PoliteClient(0, 5, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(SourceError):
        client.get("https://site.example/absente")
    assert len(calls) == 1


def test_sites_read_in_parallel_keep_the_pause_per_site() -> None:
    """Two threads on the same site wait for each other; two sites do not."""
    import threading
    import time

    times: dict[str, list[float]] = {"a.exemple.fr": [], "b.exemple.fr": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        times[request.url.host].append(time.monotonic())
        return httpx.Response(200, text="ok")

    client = PoliteClient(0.3, 5, transport=httpx.MockTransport(handler))
    start = time.monotonic()
    threads = [
        threading.Thread(target=client.get, args=(f"https://{host}/page{i}",)) for host in times for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for host_times in times.values():
        first, second = sorted(host_times)
        assert second - first >= 0.29  # the pause between two requests to one site
    assert time.monotonic() - start < 0.9  # the two sites were read at the same time
