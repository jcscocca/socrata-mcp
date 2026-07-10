import re

import httpx
import pytest

from socrata_mcp.cache import DiskCache
from socrata_mcp.config import Config
from socrata_mcp.http_client import HttpClient
from socrata_mcp.providers.socrata import SocrataProvider

DOMAIN = "data.example.gov"
DATASET = "abcd-1234"

VIEWS_PAYLOAD = {
    "id": DATASET,
    "name": "SPD Crime Data",
    "description": "Reported offenses.",
    "category": "Public Safety",
    "tags": ["crime", "police"],
    "createdAt": 1500000000,
    "rowsUpdatedAt": 1767225600,
    "viewLastModified": 1767225601,
    "attribution": "Seattle Police Department",
    "license": {"name": "Public Domain"},
    "metadata": {"custom_fields": {"Data Quality": {"Update Frequency": "Daily"}}},
    "columns": [
        {
            "fieldName": "offense_id",
            "name": "Offense ID",
            "dataTypeName": "text",
            "description": "Unique offense identifier",
        },
        {"fieldName": "offense_date", "name": "Offense Date", "dataTypeName": "calendar_date"},
        {"fieldName": "longitude", "name": "Longitude", "dataTypeName": "number"},
    ],
}

CATALOG_ENTRY = {
    "resource": {
        "id": DATASET,
        "name": "SPD Crime Data",
        "description": "Reported offenses in Seattle. " + "x" * 400,
        "updatedAt": "2026-07-01T00:00:00.000Z",
    },
    "metadata": {"domain": DOMAIN},
    "classification": {"domain_category": "Public Safety"},
    "permalink": f"https://{DOMAIN}/d/{DATASET}",
}


class FakePortal:
    """httpx.MockTransport handler emulating just enough Socrata for the tests."""

    def __init__(self):
        self.views = {DATASET: VIEWS_PAYLOAD}
        self.rows = {DATASET: []}
        self.catalog = {"results": [CATALOG_ENTRY], "resultSetSize": 1}
        self.stubs = []  # (predicate(params) -> bool, payload | callable(params) -> payload)
        self.requests = []  # (host+path, params) for every request seen
        self.fail_counts = False

    def stub(self, predicate, payload):
        self.stubs.append((predicate, payload))

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append((request.url.host + request.url.path, params))
        if request.url.host == "api.us.socrata.com" and request.url.path == "/api/catalog/v1":
            return httpx.Response(200, json=self.catalog)
        match = re.match(r"^/api/views/([\w-]+)\.json$", request.url.path)
        if match:
            payload = self.views.get(match.group(1))
            if payload is None:
                return httpx.Response(404, json={"error": True, "message": "View not found"})
            return httpx.Response(200, json=payload)
        match = re.match(r"^/resource/([\w-]+)\.json$", request.url.path)
        if match:
            return self._resource(match.group(1), params)
        return httpx.Response(404, json={"error": True, "message": "no route"})

    def _resource(self, dataset_id: str, params: dict) -> httpx.Response:
        for predicate, payload in self.stubs:
            if predicate(params):
                body = payload(params) if callable(payload) else payload
                if isinstance(body, httpx.Response):
                    return body
                return httpx.Response(200, json=body)
        rows = self.rows.get(dataset_id, [])
        select = params.get("$select", "")
        if select.replace(" ", "").startswith("count(*)"):
            if self.fail_counts:
                return httpx.Response(
                    400, json={"error": True, "message": "count not supported here"}
                )
            return httpx.Response(200, json=[{"count": str(len(rows))}])
        query = params.get("$query")
        if query is not None:
            match = re.search(r"\blimit\s+(\d+)\b", query, re.IGNORECASE)
            limit = int(match.group(1)) if match else 1000
            return httpx.Response(200, json=rows[:limit])
        limit = int(params.get("$limit", 1000))
        offset = int(params.get("$offset", 0))
        return httpx.Response(200, json=rows[offset : offset + limit])


@pytest.fixture
def fake_portal():
    return FakePortal()


@pytest.fixture
def config(tmp_path):
    return Config(
        app_token=None,
        cache_dir=tmp_path / "cache",
        metadata_ttl=300,
        catalog_ttl=300,
        query_ttl=3600,
        default_limit=100,
        max_rows=5000,
        max_export_rows=1_000_000,
        page_size=10,
        throttle_interval=0.0,
        timeout=30.0,
    )


@pytest.fixture
def provider(fake_portal, config):
    http = HttpClient(config, transport=httpx.MockTransport(fake_portal.handler))
    cache = DiskCache(config.cache_dir)
    return SocrataProvider(config=config, http=http, cache=cache)
