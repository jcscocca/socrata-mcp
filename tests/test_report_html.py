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
