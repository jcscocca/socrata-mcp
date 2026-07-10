import pytest

from socrata_mcp.errors import PortalError
from socrata_mcp.providers.base import Provider, QuerySpec
from tests.conftest import DATASET, DOMAIN


def make_rows(n):
    return [{"offense_id": str(i), "offense": "THEFT"} for i in range(n)]


class TestSearch:
    def test_maps_discovery_results(self, provider):
        result = provider.search_datasets("crime")
        assert result["total"] == 1
        (hit,) = result["results"]
        assert hit["id"] == DATASET
        assert hit["name"] == "SPD Crime Data"
        assert hit["domain"] == DOMAIN
        assert hit["category"] == "Public Safety"
        assert hit["permalink"] == f"https://{DOMAIN}/d/{DATASET}"
        assert len(hit["description"]) <= 303  # truncated + ellipsis

    def test_sends_discovery_params(self, provider, fake_portal):
        provider.search_datasets("crime", domain=DOMAIN, category="Public Safety", limit=5)
        path, params = fake_portal.requests[-1]
        assert path == "api.us.socrata.com/api/catalog/v1"
        assert params["q"] == "crime"
        assert params["domains"] == DOMAIN
        # Discovery quirk: with `domains` alone, federated portals return 0
        # hits for full-text queries; `search_context` must accompany it.
        assert params["search_context"] == DOMAIN
        assert params["categories"] == "Public Safety"
        assert params["only"] == "dataset"
        assert params["limit"] == "5"

    def test_search_is_cached(self, provider, fake_portal):
        provider.search_datasets("crime")
        provider.search_datasets("crime")
        assert len(fake_portal.requests) == 1
        provider.search_datasets("different query")
        assert len(fake_portal.requests) == 2


class TestGetDataset:
    def test_metadata_shape(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(42)
        info = provider.get_dataset(DOMAIN, DATASET)
        assert info["id"] == DATASET
        assert info["domain"] == DOMAIN
        assert info["name"] == "SPD Crime Data"
        assert info["row_count"] == 42
        assert info["license"] == "Public Domain"
        assert info["attribution"] == "Seattle Police Department"
        assert info["update_frequency"] == "Daily"
        assert info["data_updated_at"] == "2026-01-01T00:00:00+00:00"
        assert info["source_url"] == f"https://{DOMAIN}/d/{DATASET}"
        columns = {c["field_name"]: c for c in info["columns"]}
        assert columns["offense_id"]["type"] == "text"
        assert columns["offense_id"]["description"] == "Unique offense identifier"
        assert columns["offense_date"]["type"] == "calendar_date"

    def test_unknown_dataset_surfaces_portal_message(self, provider):
        with pytest.raises(PortalError) as exc_info:
            provider.get_dataset(DOMAIN, "zzzz-zzzz")
        assert "View not found" in str(exc_info.value)

    def test_row_count_failure_degrades_to_none(self, provider, fake_portal):
        fake_portal.fail_counts = True
        info = provider.get_dataset(DOMAIN, DATASET)
        assert info["row_count"] is None
        assert "count not supported here" in info["notes"][0]


class TestQuery:
    def test_pages_through_results(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(25)
        result = provider.query(DOMAIN, DATASET, QuerySpec())
        assert result["row_count"] == 25
        assert result["truncated"] is False
        offsets = [
            p["$offset"]
            for path, p in fake_portal.requests
            if "/resource/" in path and "$offset" in p
        ]
        assert offsets == ["0", "10", "20"]

    def test_truncation_at_limit(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(25)
        result = provider.query(DOMAIN, DATASET, QuerySpec(limit=20))
        assert result["row_count"] == 20
        assert result["truncated"] is True

    def test_offset_respected(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(30)
        result = provider.query(DOMAIN, DATASET, QuerySpec(limit=10, offset=5))
        assert [r["offense_id"] for r in result["rows"]][:3] == ["5", "6", "7"]
        assert result["row_count"] == 10

    def test_raw_soql_single_request_with_truncation(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(25)
        result = provider.query(
            DOMAIN, DATASET, QuerySpec(soql="SELECT offense_id LIMIT 20")
        )
        assert result["row_count"] == 20
        assert result["truncated"] is True
        resource_requests = [
            (path, p) for path, p in fake_portal.requests if "/resource/" in path
        ]
        assert len(resource_requests) == 1
        assert resource_requests[0][1]["$query"] == "SELECT offense_id LIMIT 21"

    def test_query_results_cached(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(3)
        provider.query(DOMAIN, DATASET, QuerySpec(limit=5))
        before = len(fake_portal.requests)
        result = provider.query(DOMAIN, DATASET, QuerySpec(limit=5))
        assert len(fake_portal.requests) == before
        assert result["row_count"] == 3

    def test_clamped_limit_reported(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(3)
        result = provider.query(DOMAIN, DATASET, QuerySpec(limit=99_999))
        assert result["query"]["clamped"] is True
        assert result["query"]["effective_limit"] == 5000


def test_socrata_provider_implements_interface(provider):
    assert isinstance(provider, Provider)
