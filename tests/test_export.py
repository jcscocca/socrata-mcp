import csv

from socrata_mcp.providers.base import QuerySpec
from tests.conftest import DATASET, DOMAIN


def make_rows(n):
    return [
        {"offense_id": str(i), "offense_date": f"2026-06-{i % 28 + 1:02d}T00:00:00.000"}
        for i in range(n)
    ]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


class TestSample:
    def test_returns_first_n_rows(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(25)
        result = provider.sample(DOMAIN, DATASET, n=5)
        assert result["row_count"] == 5
        assert result["rows"][0]["offense_id"] == "0"
        assert "not a random sample" in result["note"]

    def test_n_is_capped(self, provider, fake_portal):
        fake_portal.rows[DATASET] = make_rows(150)
        result = provider.sample(DOMAIN, DATASET, n=150)
        assert result["row_count"] == 100


class TestExportCsv:
    def test_full_export_uses_metadata_header(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = make_rows(25)
        out = tmp_path / "out" / "export.csv"
        result = provider.export_csv(DOMAIN, DATASET, QuerySpec(), out)
        assert result["rows_written"] == 25
        assert result["truncated"] is False
        rows = read_csv(out)
        assert rows[0] == ["offense_id", "offense_date", "longitude"]
        assert len(rows) == 26
        assert rows[1][0] == "0"
        assert rows[1][2] == ""  # longitude absent from data -> empty cell

    def test_projection_header_from_first_page(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = [
            {"offense_id": "1", "location": {"latitude": "47.6", "longitude": "-122.3"}},
            {"offense_id": "2"},
        ]
        out = tmp_path / "export.csv"
        provider.export_csv(
            DOMAIN, DATASET, QuerySpec(select=["offense_id", "location"]), out
        )
        rows = read_csv(out)
        assert rows[0] == ["offense_id", "location"]
        assert '"latitude"' in rows[1][1]  # dict serialized as JSON
        assert rows[2] == ["2", ""]

    def test_max_rows_truncates(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = make_rows(25)
        out = tmp_path / "export.csv"
        result = provider.export_csv(DOMAIN, DATASET, QuerySpec(), out, max_rows=10)
        assert result["rows_written"] == 10
        assert result["truncated"] is True
        assert len(read_csv(out)) == 11

    def test_spec_limit_respected(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = make_rows(25)
        out = tmp_path / "export.csv"
        result = provider.export_csv(DOMAIN, DATASET, QuerySpec(limit=7), out)
        assert result["rows_written"] == 7
        assert result["truncated"] is True

    def test_raw_soql_export(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = make_rows(25)
        out = tmp_path / "export.csv"
        result = provider.export_csv(
            DOMAIN, DATASET, QuerySpec(soql="SELECT offense_id LIMIT 12"), out
        )
        assert result["rows_written"] == 12
        assert result["truncated"] is True

    def test_export_pages_through_results(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = make_rows(25)
        provider.export_csv(DOMAIN, DATASET, QuerySpec(), tmp_path / "export.csv")
        offsets = [
            p["$offset"]
            for path, p in fake_portal.requests
            if "/resource/" in path and "$offset" in p
        ]
        assert offsets[0] == "0"
        assert len(offsets) >= 3
