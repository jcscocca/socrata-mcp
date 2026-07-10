"""Live end-to-end smoke tests against data.seattle.gov (SPD Crime Data).

Deselected by default (pytest addopts = -m 'not network'). Run with:
    pytest -m network --no-header -rN
"""

import csv
from datetime import datetime, timedelta, timezone

import pytest

from socrata_mcp.cache import DiskCache
from socrata_mcp.config import Config
from socrata_mcp.http_client import HttpClient
from socrata_mcp.providers.base import QuerySpec
from socrata_mcp.providers.socrata import SocrataProvider

pytestmark = pytest.mark.network

DOMAIN = "data.seattle.gov"
DATASET = "tazs-3rd5"  # SPD Crime Data 2008-Present
DATE_FIELD = "offense_start_datetime"


@pytest.fixture(scope="module")
def live_provider(tmp_path_factory):
    config = Config.from_env(env={"SOCRATA_MCP_CACHE_DIR": str(tmp_path_factory.mktemp("cache"))})
    return SocrataProvider(
        config=config, http=HttpClient(config), cache=DiskCache(config.cache_dir)
    )


def last_30_days_where():
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return f"{DATE_FIELD} > '{cutoff.strftime('%Y-%m-%dT%H:%M:%S')}'"


def test_search_seattle_portal(live_provider):
    result = live_provider.search_datasets("crime", domain=DOMAIN)
    assert result["count"] > 0
    assert all(hit["domain"] == DOMAIN for hit in result["results"])
    assert any(hit["id"] == DATASET for hit in result["results"]), (
        "SPD Crime Data should appear in a 'crime' search on data.seattle.gov"
    )


def test_get_dataset_spd_crime(live_provider):
    info = live_provider.get_dataset(DOMAIN, DATASET)
    assert info["name"]
    assert info["row_count"] > 100_000
    fields = {c["field_name"] for c in info["columns"]}
    assert DATE_FIELD in fields
    assert "offense" in fields
    date_col = next(c for c in info["columns"] if c["field_name"] == DATE_FIELD)
    assert date_col["type"] == "calendar_date"
    assert info["data_updated_at"] is not None


def test_profile_spd_crime(live_provider):
    profile = live_provider.profile_dataset(DOMAIN, DATASET)
    assert profile["row_count"] > 100_000
    cols = {c["field_name"]: c for c in profile["columns"]}
    offense = cols["offense"]
    assert offense.get("distinct_count", 0) > 10
    assert offense.get("top_values"), "offense is low-cardinality text; expect top values"
    assert 0 <= offense["null_rate"] <= 1
    date_col = cols[DATE_FIELD]
    assert date_col.get("min", "") < date_col.get("max", "")


def test_query_last_30_days(live_provider):
    result = live_provider.query(
        DOMAIN,
        DATASET,
        QuerySpec(where=last_30_days_where(), order=f"{DATE_FIELD} DESC", limit=50),
    )
    assert 0 < result["row_count"] <= 50
    cutoff = (datetime.now(timezone.utc) - timedelta(days=31)).strftime("%Y-%m-%d")
    for row in result["rows"]:
        assert row[DATE_FIELD] >= cutoff


def test_export_last_30_days_csv(live_provider, tmp_path):
    out = tmp_path / "spd_last_30_days.csv"
    result = live_provider.export_csv(
        DOMAIN,
        DATASET,
        QuerySpec(where=last_30_days_where(), order=f"{DATE_FIELD} DESC"),
        out,
        max_rows=20_000,
    )
    assert result["rows_written"] > 100
    with out.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert "offense" in rows[0]
    assert len(rows) == result["rows_written"] + 1
