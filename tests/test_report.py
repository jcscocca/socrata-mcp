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
