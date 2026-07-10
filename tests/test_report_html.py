import xml.etree.ElementTree as ET

from socrata_mcp.report_html import (
    _compact,
    _ticks,
    bar_chart_svg,
    column_chart_svg,
)


class TestCompact:
    def test_values(self):
        assert _compact(0) == "0"
        assert _compact(950) == "950"
        assert _compact(1284) == "1.3K"
        assert _compact(25000) == "25K"
        assert _compact(2654356) == "2.7M"

    def test_unit_promotion_at_rounding_boundary(self):
        assert _compact(999) == "1K"
        assert _compact(999950) == "1M"


class TestTicks:
    def test_covers_max_with_round_steps(self):
        ticks = _ticks(103051)
        assert ticks[0] == 0
        assert ticks[-1] >= 103051
        steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
        assert len(steps) == 1  # uniform spacing

    def test_zero_max(self):
        assert _ticks(0) == [0.0]

    def test_small_integer_max_keeps_integer_steps(self):
        assert _ticks(1) == [0.0, 1.0]
        assert _ticks(2) == [0.0, 1.0, 2.0]


class TestColumnChart:
    def test_renders_one_bar_per_point_and_parses(self):
        svg = column_chart_svg(
            [("2024", 10), ("2025", 0), ("2026", 20)], aria_label="test & chart"
        )
        assert svg.count('class="bar"') == 2  # zero-height bar is skipped
        assert "&amp;" in svg
        ET.fromstring(svg)  # well-formed XML

    def test_labels_thinned_for_many_points(self):
        points = [(str(2000 + i), i + 1) for i in range(100)]
        svg = column_chart_svg(points, aria_label="x")
        assert svg.count('text-anchor="middle"') < 60  # not one label per bar
        ET.fromstring(svg)

    def test_empty_points_render_empty_chart(self):
        svg = column_chart_svg([], aria_label="empty")
        ET.fromstring(svg)
        assert 'class="bar"' not in svg


class TestBarChart:
    def test_escapes_and_truncates_labels(self):
        svg = bar_chart_svg(
            [("<b>bold</b>", 10), ("x" * 60, 5)], aria_label="top values"
        )
        assert "<b>" not in svg
        assert "&lt;b&gt;" in svg
        assert "…" in svg
        ET.fromstring(svg)


from socrata_mcp.report_html import render_html  # noqa: E402


def make_model(**overrides):
    model = {
        "title": "Test <Dataset>",
        "domain": "data.example.gov",
        "dataset_id": "abcd-1234",
        "source_url": "https://data.example.gov/d/abcd-1234",
        "row_count": 1000,
        "update_frequency": "Daily",
        "license": "Public Domain",
        "attribution": "Example Dept",
        "data_updated_at": "2026-07-01T00:00:00+00:00",
        "generated_at": "2026-07-10 12:00 UTC",
        "where": None,
        "date_span": {
            "field": "occ_date",
            "min": "2019-01-01T00:00:00.000",
            "max": "2026-06-01T00:00:00.000",
        },
        "sections": ["trend", "categories", "numeric", "quality"],
        "trend": {
            "field": "occ_date",
            "granularity": "year",
            "points": [{"bucket": "2025", "n": 20}, {"bucket": "2026", "n": 10}],
        },
        "categories": [
            {
                "field_name": "status",
                "distinct_count": 3,
                "null_rate": 0.0,
                "values": [
                    {"value": "OPEN", "count": 700},
                    {"value": None, "count": 300},
                ],
                "coverage": 0.9,
            }
        ],
        "numeric": [
            {
                "field_name": "lon",
                "min": -122.4,
                "max": -122.2,
                "avg": -122.3,
                "null_rate": 0.2,
            }
        ],
        "quality": {
            "null_rates": [
                {
                    "field_name": "status",
                    "type": "text",
                    "null_rate": 0.0,
                    "distinct_count": 3,
                }
            ],
            "flags": [
                {
                    "field_name": "status",
                    "flag": "case_variants",
                    "detail": "values 'N' and 'n' differ only by case/whitespace",
                }
            ],
            "profile_notes": ["broken: portal said no"],
        },
        "queries": ["SELECT count(*) LIMIT 200"],
        "notes": ["example note"],
        "version": "0.1.0",
    }
    model.update(overrides)
    return model


class TestRenderHtml:
    def test_sections_and_escaping(self):
        out = render_html(make_model())
        assert out.startswith("<!doctype html>")
        for section in ("trend", "categories", "numeric", "quality"):
            assert f'id="{section}"' in out
        assert "&lt;Dataset&gt;" in out and "<Dataset>" not in out
        assert "(null)" in out
        assert "example note" in out
        assert "Case-variant values" in out
        assert "broken: portal said no" in out
        assert "SELECT count(*) LIMIT 200" in out
        assert "socrata-mcp 0.1.0" in out
        assert "<script" not in out  # no JS anywhere

    def test_month_buckets_shortened(self):
        model = make_model(
            trend={
                "field": "occ_date",
                "granularity": "month",
                "points": [{"bucket": "2026-01-01T00:00:00.000", "n": 5}],
            }
        )
        out = render_html(model)
        assert ">2026-01<" in out

    def test_omitted_sections_not_rendered(self):
        model = make_model(sections=["quality"], trend=None, categories=[], numeric=[])
        out = render_html(model)
        assert 'id="quality"' in out
        assert 'id="trend"' not in out
        assert 'id="numeric"' not in out

    def test_where_filter_shown(self):
        out = render_html(make_model(where="occ_date >= '2025-01-01'"))
        assert "occ_date &gt;= &#x27;2025-01-01&#x27;" in out

    def test_where_filter_hidden_without_trend(self):
        model = make_model(
            where="occ_date >= '2025-01-01'",
            sections=["quality"], trend=None, categories=[], numeric=[],
        )
        out = render_html(model)
        assert "Filter:" not in out

    def test_data_updated_at_in_header(self):
        out = render_html(make_model())
        assert "data updated 2026-07-01" in out
        assert "data updated" not in render_html(make_model(data_updated_at=None))
