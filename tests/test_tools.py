import json

import pytest
from mcp.shared.memory import (
    create_connected_server_and_client_session as client_session,
)

from socrata_mcp.mcp import tools
from socrata_mcp.mcp.app import server
from tests.conftest import DATASET, DOMAIN

EXPECTED_TOOLS = {
    "search_datasets",
    "get_dataset",
    "query",
    "profile_dataset",
    "sample",
    "export_csv",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_provider(provider):
    tools.set_provider(provider)
    yield provider
    tools.set_provider(None)


def result_json(result):
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_all_tools_registered(mcp_provider):
    async with client_session(server._mcp_server) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_search_datasets_tool(mcp_provider):
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool("search_datasets", {"query": "crime"})
        data = result_json(result)
        assert data["results"][0]["id"] == DATASET


@pytest.mark.anyio
async def test_get_dataset_tool(mcp_provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": "1"}]
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "get_dataset", {"domain": DOMAIN, "dataset_id": DATASET}
        )
        data = result_json(result)
        assert data["name"] == "SPD Crime Data"
        assert data["row_count"] == 1


@pytest.mark.anyio
async def test_query_tool_structured(mcp_provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(30)]
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "query", {"domain": DOMAIN, "dataset_id": DATASET, "limit": 10}
        )
        data = result_json(result)
        assert data["row_count"] == 10
        assert data["truncated"] is True


@pytest.mark.anyio
async def test_query_tool_within_circle(mcp_provider, fake_portal):
    fake_portal.rows[DATASET] = []
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "query",
            {
                "domain": DOMAIN,
                "dataset_id": DATASET,
                "within_circle": {
                    "field": "location",
                    "lat": 47.6,
                    "lon": -122.3,
                    "radius_m": 500,
                },
            },
        )
        assert not result.isError
    wheres = [p.get("$where") for _, p in fake_portal.requests if "$where" in p]
    assert wheres and "within_circle(location, 47.6, -122.3, 500)" in wheres[0]


@pytest.mark.anyio
async def test_query_tool_validation_error_is_loud(mcp_provider):
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "query",
            {
                "domain": DOMAIN,
                "dataset_id": DATASET,
                "soql": "SELECT * LIMIT 5",
                "where": "a = 1",
            },
        )
        assert result.isError
        assert "not both" in result.content[0].text


@pytest.mark.anyio
async def test_profile_tool(mcp_provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(100)]
    fake_portal.stub(
        lambda p: "count(distinct" in p.get("$select", ""),
        [{"nn_0": "90", "d_0": "900", "nn_1": "100", "d_1": "50",
          "mn_1": "2018-01-01T00:00:00.000", "mx_1": "2026-06-30T00:00:00.000",
          "nn_2": "80", "d_2": "70", "mn_2": "-122.4", "mx_2": "-122.2",
          "av_2": "-122.3"}],
    )
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "profile_dataset", {"domain": DOMAIN, "dataset_id": DATASET}
        )
        data = result_json(result)
        assert data["row_count"] == 100
        assert len(data["columns"]) == 3


@pytest.mark.anyio
async def test_sample_tool(mcp_provider, fake_portal):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(30)]
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "sample", {"domain": DOMAIN, "dataset_id": DATASET, "n": 3}
        )
        data = result_json(result)
        assert data["row_count"] == 3


@pytest.mark.anyio
async def test_export_csv_tool(mcp_provider, fake_portal, tmp_path):
    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(5)]
    out = tmp_path / "export.csv"
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "export_csv",
            {"domain": DOMAIN, "dataset_id": DATASET, "out_path": str(out)},
        )
        data = result_json(result)
        assert data["rows_written"] == 5
        assert out.exists()
