import httpx
import pytest

from socrata_mcp.config import Config
from socrata_mcp.errors import PortalError
from socrata_mcp.http_client import HttpClient


def make_config(**overrides):
    base = dict(
        app_token=None,
        cache_dir=None,
        metadata_ttl=300,
        catalog_ttl=300,
        query_ttl=3600,
        default_limit=100,
        max_rows=5000,
        max_export_rows=1_000_000,
        page_size=1000,
        throttle_interval=0.0,
        timeout=30.0,
    )
    base.update(overrides)
    return Config(**base)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make_client(handler, config=None, clock=None, sleeps=None):
    clock = clock or FakeClock()
    sleeps = sleeps if sleeps is not None else []

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    return HttpClient(
        config or make_config(),
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=sleep,
    )


def test_sends_app_token_and_params():
    seen = {}

    def handler(request):
        seen["token"] = request.headers.get("X-App-Token")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"ok": 1}])

    client = make_client(handler, config=make_config(app_token="tok123"))
    data = client.get_json("https://data.example.gov/resource/abcd-1234.json", {"$limit": 2})
    assert data == [{"ok": 1}]
    assert seen["token"] == "tok123"
    assert "%24limit=2" in seen["url"] or "$limit=2" in seen["url"]


def test_no_token_header_when_unset():
    seen = {}

    def handler(request):
        seen["token"] = request.headers.get("X-App-Token")
        return httpx.Response(200, json={})

    make_client(handler).get_json("https://data.example.gov/x.json")
    assert seen["token"] is None


def test_portal_error_message_surfaced():
    def handler(request):
        return httpx.Response(
            400,
            json={"code": "query.soql.no-such-column", "error": True,
                  "message": "No such column: bogus"},
        )

    client = make_client(handler)
    with pytest.raises(PortalError) as exc_info:
        client.get_json("https://data.example.gov/resource/abcd-1234.json")
    err = exc_info.value
    assert err.portal_message == "No such column: bogus"
    assert err.status == 400
    assert "No such column: bogus" in str(err)


def test_retries_429_honoring_retry_after():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, json={})
        return httpx.Response(200, json={"ok": True})

    sleeps = []
    client = make_client(handler, sleeps=sleeps)
    assert client.get_json("https://data.example.gov/x.json") == {"ok": True}
    assert len(attempts) == 2
    assert 3.0 in sleeps


def test_retries_exhausted_raises_portal_error():
    def handler(request):
        return httpx.Response(503, text="Service Unavailable")

    client = make_client(handler)
    with pytest.raises(PortalError) as exc_info:
        client.get_json("https://data.example.gov/x.json")
    assert exc_info.value.status == 503


def test_connect_error_retried_then_succeeds():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    assert client.get_json("https://data.example.gov/x.json") == {"ok": True}
    assert len(attempts) == 3


def test_throttle_spaces_requests():
    def handler(request):
        return httpx.Response(200, json={})

    sleeps = []
    client = make_client(
        handler, config=make_config(throttle_interval=0.5), sleeps=sleeps
    )
    client.get_json("https://data.example.gov/a.json")
    client.get_json("https://data.example.gov/b.json")
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.5


def test_max_attempts_override_limits_retries():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.ConnectError("boom")

    client = make_client(handler)
    with pytest.raises(PortalError) as exc_info:
        client.get_json("https://data.example.gov/x.json", max_attempts=2)
    assert len(attempts) == 2
    assert "after 2 attempts" in str(exc_info.value)
