from socrata_mcp.report import is_id_like, pick_category_columns, pick_date_column


def col(field, ctype="text", **stats):
    return {"field_name": field, "type": ctype, **stats}


def date_col(field, null_rate, lo, hi):
    return col(field, "calendar_date", null_rate=null_rate, min=lo, max=hi)


class TestPickDateColumn:
    def test_lowest_null_rate_wins(self):
        cols = [
            date_col("a", 0.2, "2003-01-01T00:00:00.000", "2026-01-01T00:00:00.000"),
            date_col("b", 0.0, "2024-01-01T00:00:00.000", "2026-01-01T00:00:00.000"),
        ]
        assert pick_date_column(cols)["field_name"] == "b"

    def test_tie_broken_by_widest_span(self):
        cols = [
            date_col("narrow", 0.0, "2024-01-01T00:00:00.000", "2026-01-01T00:00:00.000"),
            date_col("wide", 0.0, "2003-01-01T00:00:00.000", "2026-01-01T00:00:00.000"),
        ]
        assert pick_date_column(cols)["field_name"] == "wide"

    def test_skips_mostly_null_and_broken_columns(self):
        cols = [
            date_col("nully", 0.9, "2003-01-01T00:00:00.000", "2026-01-01T00:00:00.000"),
            col("no_minmax", "calendar_date", null_rate=0.0),
            col("not_a_date", "text", null_rate=0.0, min="a", max="z"),
        ]
        assert pick_date_column(cols) is None


class TestIsIdLike:
    def test_distinct_equals_rows(self):
        assert is_id_like(col("id", distinct_count=1000), 1000) is True

    def test_small_datasets_never_id_like(self):
        assert is_id_like(col("id", distinct_count=50), 50) is False

    def test_unknown_row_count(self):
        assert is_id_like(col("id", distinct_count=1000), None) is False


class TestPickCategoryColumns:
    def test_ranked_null_rate_asc_then_distinct_desc(self):
        top = [{"value": "A", "count": 10}]
        cols = [
            col("few", null_rate=0.0, distinct_count=3, top_values=top),
            col("many", null_rate=0.0, distinct_count=40, top_values=top),
            col("nully", null_rate=0.1, distinct_count=10, top_values=top),
        ]
        picked = [c["field_name"] for c in pick_category_columns(cols, 1000)]
        assert picked == ["many", "few", "nully"]

    def test_exclusions(self):
        top = [{"value": "A", "count": 10}]
        cols = [
            col("constant", null_rate=0.0, distinct_count=1, top_values=top),
            col("too_many", null_rate=0.0, distinct_count=51, top_values=top),
            col("mostly_null", null_rate=0.6, distinct_count=5, top_values=top),
            col("id_like", null_rate=0.0, distinct_count=1000, top_values=top),
            col("no_top_values", null_rate=0.0, distinct_count=5),
            col("numeric", "number", null_rate=0.0, distinct_count=5, top_values=top),
        ]
        assert pick_category_columns(cols, 1000) == []

    def test_caps_at_three(self):
        top = [{"value": "A", "count": 10}]
        cols = [
            col(f"c{i}", null_rate=0.0, distinct_count=10 - i, top_values=top)
            for i in range(4)
        ]
        assert len(pick_category_columns(cols, 1000)) == 3


from socrata_mcp.report import (  # noqa: E402
    describe_query,
    find_landmines,
    numeric_summary,
    trend_spec,
)


class TestFindLandmines:
    def test_mostly_null_at_threshold(self):
        flags = find_landmines([col("a", null_rate=0.5)], 1000)
        assert [f["flag"] for f in flags] == ["mostly_null"]
        assert find_landmines([col("a", null_rate=0.49)], 1000) == []

    def test_constant(self):
        flags = find_landmines([col("a", null_rate=0.0, distinct_count=1)], 1000)
        assert [f["flag"] for f in flags] == ["constant"]

    def test_id_like(self):
        flags = find_landmines([col("a", null_rate=0.0, distinct_count=1000)], 1000)
        assert [f["flag"] for f in flags] == ["id_like"]
        assert find_landmines([col("a", null_rate=0.0, distinct_count=50)], 50) == []

    def test_case_variants(self):
        top = [{"value": "N", "count": 900}, {"value": "n", "count": 10}]
        flags = find_landmines([col("a", null_rate=0.0, distinct_count=2, top_values=top)], 1000)
        assert [f["flag"] for f in flags] == ["case_variants"]
        assert "'N'" in flags[0]["detail"] and "'n'" in flags[0]["detail"]

    def test_whitespace_variants_flagged_distinct_values_not(self):
        ws = [{"value": "N", "count": 900}, {"value": " N", "count": 10}]
        assert [f["flag"] for f in find_landmines(
            [col("a", null_rate=0.0, distinct_count=2, top_values=ws)], 1000
        )] == ["case_variants"]
        clean = [{"value": "Y", "count": 900}, {"value": "N", "count": 100}]
        assert find_landmines(
            [col("a", null_rate=0.0, distinct_count=2, top_values=clean)], 1000
        ) == []


class TestTrendSpec:
    def test_long_span_buckets_by_year(self):
        spec_args, granularity = trend_spec(
            date_col("occ", 0.0, "2019-01-01T00:00:00.000", "2026-06-01T00:00:00.000"),
            None,
        )
        assert granularity == "year"
        assert spec_args["select"] == ["date_extract_y(occ) as bucket", "count(*) as n"]
        assert spec_args["group"] == ["bucket"]
        assert spec_args["order"] == "bucket DESC"
        assert spec_args["limit"] == 200
        assert spec_args["where"] is None

    def test_short_span_buckets_by_month(self):
        spec_args, granularity = trend_spec(
            date_col("occ", 0.0, "2025-01-01T00:00:00.000", "2026-06-01T00:00:00.000"),
            "occ >= '2025-01-01'",
        )
        assert granularity == "month"
        assert spec_args["select"][0] == "date_trunc_ym(occ) as bucket"
        assert spec_args["where"] == "occ >= '2025-01-01'"


class TestNumericSummary:
    def test_includes_numeric_with_stats_only(self):
        cols = [
            col("lon", "number", null_rate=0.1, min=-122.4, max=-122.2, avg=-122.3),
            col("no_stats", "number", null_rate=0.1),
            col("idnum", "number", null_rate=0.0, distinct_count=1000, min=1, max=1000, avg=500),
            col("words", "text", null_rate=0.0, min="a", max="z"),
        ]
        out = numeric_summary(cols, 1000)
        assert [c["field_name"] for c in out] == ["lon"]
        assert out[0]["min"] == -122.4 and out[0]["null_rate"] == 0.1


class TestDescribeQuery:
    def test_renders_readable_soql(self):
        text = describe_query(
            {
                "$select": "date_extract_y(occ) as bucket, count(*) as n",
                "$where": "occ >= '2025-01-01'",
                "$group": "bucket",
                "$order": "bucket DESC",
            },
            200,
        )
        assert text == (
            "SELECT date_extract_y(occ) as bucket, count(*) as n "
            "WHERE occ >= '2025-01-01' GROUP BY bucket ORDER BY bucket DESC LIMIT 200"
        )
