import shutil

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

    def test_case_variants_single_flag_per_column(self):
        top = [
            {"value": "N", "count": 900},
            {"value": "n", "count": 10},
            {"value": " N", "count": 5},
        ]
        flags = find_landmines(
            [col("a", null_rate=0.0, distinct_count=3, top_values=top)], 1000
        )
        assert [f["flag"] for f in flags] == ["case_variants"]

    def test_id_like_columns_skip_case_variant_scan(self):
        top = [{"value": "A", "count": 900}, {"value": "a", "count": 100}]
        flags = find_landmines(
            [col("a", null_rate=0.0, distinct_count=1000, top_values=top)], 1000
        )
        assert [f["flag"] for f in flags] == ["id_like"]

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


from socrata_mcp.report import TREND_MAX_POINTS, build_report  # noqa: E402

METADATA = {
    "id": "abcd-1234",
    "domain": "data.example.gov",
    "name": "SPD Crime Data",
    "source_url": "https://data.example.gov/d/abcd-1234",
    "update_frequency": "Daily",
    "license": "Public Domain",
    "attribution": "Seattle Police Department",
    "data_updated_at": "2026-07-01T00:00:00+00:00",
}


def make_profile():
    return {
        "row_count": 1000,
        "notes": ["profiled first 50 of 60 columns"],
        "columns": [
            date_col("occ_date", 0.0, "2019-01-01T00:00:00.000", "2026-06-01T00:00:00.000"),
            col(
                "status",
                null_rate=0.0,
                distinct_count=3,
                non_null_count=1000,
                top_values=[{"value": "OPEN", "count": 700}, {"value": "CLOSED", "count": 250}],
            ),
            col("lon", "number", null_rate=0.1, min=-122.4, max=-122.2, avg=-122.3),
            col("broken", error="portal said no"),
        ],
    }


def build(**overrides):
    profile = make_profile()
    kwargs = dict(
        trend_rows=[{"bucket": "2026", "n": "100"}, {"bucket": "2025", "n": "150"}],
        granularity="year",
        date_col=profile["columns"][0],
        where=None,
        queries=["SELECT ... LIMIT 200"],
        generated_at="2026-07-10 12:00 UTC",
        title=None,
        version="0.1.0",
    )
    kwargs.update(overrides)
    return build_report(METADATA, profile, **kwargs)


class TestBuildReport:
    def test_happy_path_model(self):
        model = build()
        assert model["sections"] == ["trend", "categories", "numeric", "quality"]
        assert model["title"] == "SPD Crime Data"
        assert model["trend"]["points"] == [
            {"bucket": "2025", "n": 150},
            {"bucket": "2026", "n": 100},
        ]
        assert model["categories"][0]["field_name"] == "status"
        assert model["categories"][0]["coverage"] == 0.95
        assert model["numeric"][0]["field_name"] == "lon"
        assert model["quality"]["null_rates"][0]["field_name"] in {"occ_date", "status", "lon", "broken"}
        assert "broken: portal said no" in model["quality"]["profile_notes"]
        assert "profiled first 50 of 60 columns" in model["quality"]["profile_notes"]
        assert model["queries"] == ["SELECT ... LIMIT 200"]
        assert model["notes"] == []

    def test_no_date_column(self):
        model = build(trend_rows=None, granularity=None, date_col=None, queries=[])
        assert "trend" not in model["sections"]
        assert model["trend"] is None
        assert model["date_span"] is None
        assert any("no usable date column" in n for n in model["notes"])

    def test_empty_trend_rows(self):
        model = build(trend_rows=[])
        assert "trend" not in model["sections"]
        assert any("trend query returned no rows" in n for n in model["notes"])

    def test_null_buckets_dropped(self):
        model = build(trend_rows=[{"bucket": None, "n": "5"}, {"bucket": "2026", "n": "100"}])
        assert model["trend"]["points"] == [{"bucket": "2026", "n": 100}]

    def test_truncation_note(self):
        model = build(trend_rows=[{"bucket": "2026", "n": "1"}], trend_truncated=True)
        assert any("truncated" in n for n in model["notes"])

    def test_exactly_max_points_without_flag_is_not_truncated(self):
        rows = [{"bucket": str(3000 - i), "n": "1"} for i in range(TREND_MAX_POINTS)]
        model = build(trend_rows=rows)
        assert not any("truncated" in n for n in model["notes"])

    def test_where_note_and_title_override(self):
        model = build(where="occ_date >= '2025-01-01'", title="Custom")
        assert model["title"] == "Custom"
        assert model["where"] == "occ_date >= '2025-01-01'"
        assert any("filtered by `where`" in n for n in model["notes"])

    def test_where_without_date_column_no_filter_note(self):
        model = build(
            trend_rows=None, granularity=None, date_col=None, queries=[],
            where="occ_date >= '2025-01-01'",
        )
        assert not any("filtered by `where`" in n for n in model["notes"])
        assert any("no usable date column" in n for n in model["notes"])


from tests.conftest import DATASET, DOMAIN, VIEWS_PAYLOAD  # noqa: E402


def stub_profile_aggregates(fake_portal):
    """Aggregate + top-values stubs matching conftest's 3-column schema.

    offense_id: text, high-cardinality but not id-like (249 of 250); top
    values have a case-variant pair. offense_date: 2019->2026 span (year
    granularity). longitude: numeric with stats.
    """
    fake_portal.stub(
        lambda p: "count(distinct" in p.get("$select", ""),
        [
            {
                "nn_0": "250", "d_0": "249",
                "nn_1": "250", "d_1": "12",
                "mn_1": "2019-01-01T00:00:00.000",
                "mx_1": "2026-06-01T00:00:00.000",
                "nn_2": "200", "d_2": "180",
                "mn_2": "-122.4", "mx_2": "-122.2", "av_2": "-122.3",
            }
        ],
    )
    fake_portal.stub(
        lambda p: p.get("$group") == "offense_id",
        [{"offense_id": "A", "count": "200"}, {"offense_id": "a", "count": "50"}],
    )


class TestGenerateReport:
    def test_writes_report_and_returns_sections(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(250)]
        stub_profile_aggregates(fake_portal)
        fake_portal.stub(
            lambda p: "date_extract_y" in p.get("$select", ""),
            [{"bucket": "2026", "n": "100"}, {"bucket": "2025", "n": "150"}],
        )
        out = tmp_path / "reports" / "spd.html"
        result = provider.generate_report(DOMAIN, DATASET, out)
        assert out.exists()
        assert not out.with_name(out.name + ".tmp").exists()
        assert result["path"] == str(out)
        assert "trend" in result["sections"]
        assert "quality" in result["sections"]
        assert len(result["queries"]) == 1 and "date_extract_y" in result["queries"][0]
        text = out.read_text(encoding="utf-8")
        assert "<svg" in text
        assert "SPD Crime Data" in text
        assert "Case-variant values" in text  # offense_id A/a landmine

    def test_where_scopes_trend_query(self, provider, fake_portal, tmp_path):
        fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(250)]
        stub_profile_aggregates(fake_portal)
        fake_portal.stub(
            lambda p: "date_extract_y" in p.get("$select", ""),
            [{"bucket": "2026", "n": "10"}],
        )
        result = provider.generate_report(
            DOMAIN, DATASET, tmp_path / "r.html", where="offense_date >= '2026-01-01'"
        )
        trend_requests = [
            p
            for _, p in fake_portal.requests
            if "date_extract_y" in p.get("$select", "")
        ]
        assert trend_requests and trend_requests[0]["$where"] == (
            "offense_date >= '2026-01-01'"
        )
        assert any("filtered by `where`" in n for n in result["notes"])

    def test_no_date_column_still_renders(self, provider, fake_portal, tmp_path):
        fake_portal.views[DATASET] = {
            **VIEWS_PAYLOAD,
            "columns": [
                c
                for c in VIEWS_PAYLOAD["columns"]
                if c["dataTypeName"] != "calendar_date"
            ],
        }
        fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(250)]
        fake_portal.stub(
            lambda p: "count(distinct" in p.get("$select", ""),
            [
                {
                    "nn_0": "250", "d_0": "250",
                    "nn_1": "200", "d_1": "180",
                    "mn_1": "-122.4", "mx_1": "-122.2", "av_1": "-122.3",
                }
            ],
        )
        fake_portal.stub(
            lambda p: p.get("$group") == "offense_id",
            [{"offense_id": "A", "count": "200"}],
        )
        out = tmp_path / "r.html"
        result = provider.generate_report(DOMAIN, DATASET, out)
        assert out.exists()
        assert "trend" not in result["sections"]
        assert result["queries"] == []
        assert any("no usable date column" in n for n in result["notes"])


class TestTrendCacheCoherence:
    def _trend_requests(self, fake_portal):
        return [
            p
            for _, p in fake_portal.requests
            if "date_extract_y" in p.get("$select", "")
        ]

    def _prime(self, fake_portal):
        fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(250)]
        stub_profile_aggregates(fake_portal)
        fake_portal.stub(
            lambda p: "date_extract_y" in p.get("$select", ""),
            [{"bucket": "2026", "n": "100"}],
        )

    def test_trend_refetched_when_data_updated_at_changes(
        self, provider, fake_portal, tmp_path
    ):
        self._prime(fake_portal)
        provider.generate_report(DOMAIN, DATASET, tmp_path / "a.html")
        assert len(self._trend_requests(fake_portal)) == 1

        # Portal publishes new data; metadata cache expires (its TTL is far
        # shorter than the query TTL) while the trend query cache does not.
        fake_portal.views[DATASET] = {**VIEWS_PAYLOAD, "rowsUpdatedAt": 1767312000}
        shutil.rmtree(provider.config.cache_dir / "metadata")
        provider.generate_report(DOMAIN, DATASET, tmp_path / "b.html")
        assert len(self._trend_requests(fake_portal)) == 2

    def test_trend_cached_when_dataset_unchanged(self, provider, fake_portal, tmp_path):
        self._prime(fake_portal)
        provider.generate_report(DOMAIN, DATASET, tmp_path / "a.html")
        provider.generate_report(DOMAIN, DATASET, tmp_path / "b.html")
        assert len(self._trend_requests(fake_portal)) == 1


class TestTrendOutlierTrimming:
    def _rows(self, pairs):
        # build_report expects most-recent-first rows
        return [{"bucket": str(b), "n": str(n)} for b, n in sorted(pairs, reverse=True)]

    def test_leading_sparse_run_trimmed_with_note(self):
        pairs = [(1900, 1), (1901, 1), (1902, 1)] + [(2020 + i, 1000) for i in range(7)]
        model = build(trend_rows=self._rows(pairs))
        buckets = [p["bucket"] for p in model["trend"]["points"]]
        assert buckets[0] == "2020" and len(buckets) == 7
        assert any(
            "trimmed 3 sparse leading buckets" in n and "before 2020" in n
            for n in model["notes"]
        )

    def test_short_sparse_run_kept(self):
        pairs = [(1900, 1), (1901, 1)] + [(2020 + i, 1000) for i in range(7)]
        model = build(trend_rows=self._rows(pairs))
        assert len(model["trend"]["points"]) == 9
        assert not any("trimmed" in n for n in model["notes"])

    def test_trim_capped_by_row_share(self):
        pairs = [(1900 + i, 4) for i in range(6)] + [(2025, 1000), (2026, 1000)]
        model = build(trend_rows=self._rows(pairs))
        assert len(model["trend"]["points"]) == 8
        assert not any("trimmed" in n for n in model["notes"])

    def test_trailing_sparse_run_trimmed(self):
        pairs = [(2019 + i, 1000) for i in range(7)] + [(2093, 1), (2094, 1), (2095, 1)]
        model = build(trend_rows=self._rows(pairs))
        buckets = [p["bucket"] for p in model["trend"]["points"]]
        assert buckets[-1] == "2025"
        assert any(
            "trimmed 3 sparse trailing buckets" in n and "after 2025" in n
            for n in model["notes"]
        )


class TestTrendDeltaAndPartial:
    def test_delta_between_last_two_complete_buckets(self):
        rows = [{"bucket": str(b), "n": str(n)} for b, n in
                [(2025, 900), (2024, 1000), (2023, 800)]]
        model = build(trend_rows=rows)  # data_updated_at is 2026: no partial
        trend = model["trend"]
        assert trend["last_partial"] is False
        assert trend["delta"] == {"pct": -0.1, "from": "2024", "to": "2025"}

    def test_partial_last_bucket_excluded_from_delta(self):
        rows = [{"bucket": str(b), "n": str(n)} for b, n in
                [(2026, 100), (2025, 900), (2024, 1000)]]
        model = build(trend_rows=rows)  # data_updated_at 2026-07-01 -> partial
        trend = model["trend"]
        assert trend["last_partial"] is True
        assert trend["delta"] == {"pct": -0.1, "from": "2024", "to": "2025"}

    def test_month_granularity_partial_detection(self):
        rows = [
            {"bucket": "2026-07-01T00:00:00.000", "n": "10"},
            {"bucket": "2026-06-01T00:00:00.000", "n": "500"},
            {"bucket": "2026-05-01T00:00:00.000", "n": "400"},
        ]
        model = build(trend_rows=rows, granularity="month")
        trend = model["trend"]
        assert trend["last_partial"] is True
        assert trend["delta"]["pct"] == 0.25

    def test_no_delta_when_too_few_complete_buckets(self):
        rows = [{"bucket": "2026", "n": "100"}, {"bucket": "2025", "n": "900"}]
        model = build(trend_rows=rows)  # 2026 partial -> one complete bucket
        assert model["trend"]["delta"] is None

    def test_no_delta_when_prior_bucket_is_zero_rows(self):
        rows = [{"bucket": "2025", "n": "900"}, {"bucket": "2024", "n": "0"}]
        model = build(trend_rows=rows)
        assert model["trend"]["delta"] is None
