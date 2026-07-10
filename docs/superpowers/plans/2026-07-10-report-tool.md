# Automatic Dataset Report Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

> **Status:** Complete — implemented on `feature/report-tool`, verified live against data.seattle.gov (2026-07-10).

**Goal:** One call (MCP tool `report` or CLI `socrata-mcp-report`) turns any Socrata dataset into a self-contained HTML report with charts, summary tables, and data-quality flags — deterministic, no model in the loop.

**Architecture:** Two new pure modules in house style: `report.py` (deterministic analysis: metadata + profile + trend rows in → plain model dict out) and `report_html.py` (model dict in → HTML string out, stdlib f-strings + hand-generated SVG). `SocrataProvider.generate_report()` orchestrates the fetches through the existing cached paths and writes the file via temp-file-then-rename. The MCP tool and the CLI are thin wrappers over the provider method.

**Tech Stack:** Python 3.11 stdlib only (no new dependencies). pytest with the existing `FakePortal` mock-transport fixtures. Spec: `docs/superpowers/specs/2026-07-10-report-tool-design.md`.

**File map:**

| File | Responsibility |
|---|---|
| Create `socrata_mcp/report.py` | Column-selection heuristics, landmine flags, trend query spec, model assembly |
| Create `socrata_mcp/report_html.py` | HTML/SVG rendering of the model |
| Create `socrata_mcp/report_cli.py` | argparse CLI entry point |
| Modify `socrata_mcp/providers/base.py` | Abstract `generate_report` on `Provider` |
| Modify `socrata_mcp/providers/socrata.py` | `generate_report` implementation |
| Modify `socrata_mcp/mcp/tools.py` | `report` MCP tool |
| Modify `pyproject.toml` | `socrata-mcp-report` script entry |
| Create `tests/test_report.py` | Analysis unit tests + provider integration tests |
| Create `tests/test_report_html.py` | Renderer unit tests |
| Create `tests/test_report_cli.py` | CLI tests |
| Modify `tests/test_tools.py` | Register + exercise the `report` tool |
| Modify `tests/test_live_smoke.py` | Live report generation against data.seattle.gov |
| Modify `README.md` | Document the tool + CLI |

Run all commands from the repo root with the venv active: `source .venv/bin/activate`.

---

### Task 1: Column-selection heuristics (`report.py` part 1)

**Files:**
- Create: `socrata_mcp/report.py`
- Test: `tests/test_report.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_report.py`:

```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'socrata_mcp.report'`

- [x] **Step 3: Write the implementation**

Create `socrata_mcp/report.py`:

```python
"""Automatic dataset report: deterministic analysis over metadata + profile.

Everything here is a pure function of the metadata, profile, and trend rows,
so the same inputs always produce the same report model. The provider
orchestrates the fetches; report_html.py renders the model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .profile import DATE_TYPES

TREND_YEAR_SPAN_DAYS = 1095  # >= ~3 years -> bucket by year, else by month
TREND_MAX_POINTS = 200
MAX_CATEGORY_SECTIONS = 3
CATEGORY_MAX_DISTINCT = 50
MAX_NULL_RATE = 0.5
IDLIKE_MIN_ROWS = 100
MOSTLY_NULL_RATE = 0.5


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def is_id_like(col: dict[str, Any], row_count: int | None) -> bool:
    """One distinct value per row — an identifier, not a category."""
    return (
        row_count is not None
        and row_count > IDLIKE_MIN_ROWS
        and col.get("distinct_count") == row_count
    )


def pick_date_column(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest null rate, then widest min->max span, then schema order."""
    candidates: list[tuple[tuple[float, int, int], dict[str, Any]]] = []
    for pos, col in enumerate(columns):
        if col.get("type") not in DATE_TYPES:
            continue
        lo, hi = _parse_date(col.get("min")), _parse_date(col.get("max"))
        if lo is None or hi is None:
            continue
        null_rate = col.get("null_rate") or 0.0
        if null_rate > MAX_NULL_RATE:
            continue
        candidates.append(((null_rate, -(hi - lo).days, pos), col))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def pick_category_columns(
    columns: list[dict[str, Any]], row_count: int | None
) -> list[dict[str, Any]]:
    """Null rate asc, then distinct desc, then schema order; at most 3."""
    candidates: list[tuple[tuple[float, int, int], dict[str, Any]]] = []
    for pos, col in enumerate(columns):
        if col.get("type") != "text" or not col.get("top_values"):
            continue
        distinct = col.get("distinct_count") or 0
        if not 2 <= distinct <= CATEGORY_MAX_DISTINCT:
            continue
        null_rate = col.get("null_rate") or 0.0
        if null_rate > MAX_NULL_RATE or is_id_like(col, row_count):
            continue
        candidates.append(((null_rate, -distinct, pos), col))
    candidates.sort(key=lambda item: item[0])
    return [col for _, col in candidates[:MAX_CATEGORY_SECTIONS]]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: PASS (9 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/report.py tests/test_report.py
git commit -m "feat: report column-selection heuristics"
```

---

### Task 2: Landmine flags, trend spec, query description (`report.py` part 2)

**Files:**
- Modify: `socrata_mcp/report.py`
- Test: `tests/test_report.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_report.py`:

```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'describe_query'`

- [x] **Step 3: Write the implementation**

In `socrata_mcp/report.py`, change the profile import to:

```python
from .profile import DATE_TYPES, NUMERIC_TYPES
```

Then append:

```python
def find_landmines(
    columns: list[dict[str, Any]], row_count: int | None
) -> list[dict[str, Any]]:
    """Heuristic data-quality flags, each a pure function of the profile."""
    flags: list[dict[str, Any]] = []
    for col in columns:
        field = col["field_name"]
        null_rate = col.get("null_rate")
        if null_rate is not None and null_rate >= MOSTLY_NULL_RATE:
            flags.append(
                {
                    "field_name": field,
                    "flag": "mostly_null",
                    "detail": f"{null_rate:.0%} of rows are null",
                }
            )
        if col.get("distinct_count") == 1:
            flags.append(
                {
                    "field_name": field,
                    "flag": "constant",
                    "detail": "single distinct value",
                }
            )
        if is_id_like(col, row_count):
            flags.append(
                {
                    "field_name": field,
                    "flag": "id_like",
                    "detail": "one distinct value per row",
                }
            )
        seen: dict[str, str] = {}
        for entry in col.get("top_values") or []:
            value = entry.get("value")
            if not isinstance(value, str):
                continue
            key = value.strip().lower()
            if key in seen and seen[key] != value:
                flags.append(
                    {
                        "field_name": field,
                        "flag": "case_variants",
                        "detail": (
                            f"values {seen[key]!r} and {value!r} differ only "
                            "by case/whitespace"
                        ),
                    }
                )
            seen.setdefault(key, value)
    return flags


def numeric_summary(
    columns: list[dict[str, Any]], row_count: int | None
) -> list[dict[str, Any]]:
    out = []
    for col in columns:
        if col.get("type") not in NUMERIC_TYPES or is_id_like(col, row_count):
            continue
        if all(col.get(k) is None for k in ("min", "max", "avg")):
            continue
        out.append(
            {k: col.get(k) for k in ("field_name", "min", "max", "avg", "null_rate")}
        )
    return out


def trend_spec(
    date_col: dict[str, Any], where: str | None
) -> tuple[dict[str, Any], str]:
    """Query-spec kwargs and granularity for the trend query."""
    lo, hi = _parse_date(date_col["min"]), _parse_date(date_col["max"])
    field = date_col["field_name"]
    if (hi - lo).days >= TREND_YEAR_SPAN_DAYS:
        bucket, granularity = f"date_extract_y({field})", "year"
    else:
        bucket, granularity = f"date_trunc_ym({field})", "month"
    return (
        {
            "select": [f"{bucket} as bucket", "count(*) as n"],
            "where": where,
            "group": ["bucket"],
            "order": "bucket DESC",
            "limit": TREND_MAX_POINTS,
        },
        granularity,
    )


def describe_query(params: dict[str, str], limit: int) -> str:
    """Readable SoQL for the report footer, from built query params."""
    parts = []
    if "$select" in params:
        parts.append("SELECT " + params["$select"])
    if "$where" in params:
        parts.append("WHERE " + params["$where"])
    if "$group" in params:
        parts.append("GROUP BY " + params["$group"])
    if "$order" in params:
        parts.append("ORDER BY " + params["$order"])
    parts.append(f"LIMIT {limit}")
    return " ".join(parts)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: PASS (18 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/report.py tests/test_report.py
git commit -m "feat: landmine flags, trend spec, query description"
```

---

### Task 3: Model assembly — `build_report` (`report.py` part 3)

**Files:**
- Modify: `socrata_mcp/report.py`
- Test: `tests/test_report.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_report.py`:

```python
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
        rows = [{"bucket": str(3000 - i), "n": "1"} for i in range(TREND_MAX_POINTS)]
        model = build(trend_rows=rows)
        assert any("most recent" in n for n in model["notes"])

    def test_where_note_and_title_override(self):
        model = build(where="occ_date >= '2025-01-01'", title="Custom")
        assert model["title"] == "Custom"
        assert model["where"] == "occ_date >= '2025-01-01'"
        assert any("filtered by `where`" in n for n in model["notes"])
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_report'`

- [x] **Step 3: Write the implementation**

Append to `socrata_mcp/report.py`:

```python
def build_report(
    metadata: dict[str, Any],
    profile: dict[str, Any],
    *,
    trend_rows: list[dict[str, Any]] | None,
    granularity: str | None,
    date_col: dict[str, Any] | None,
    where: str | None,
    queries: list[str],
    generated_at: str,
    title: str | None = None,
    version: str = "dev",
) -> dict[str, Any]:
    """Assemble the report model. Pure: no I/O, no clocks."""
    columns = profile.get("columns", [])
    row_count = profile.get("row_count")
    notes: list[str] = []
    sections: list[str] = []

    trend = None
    if date_col is None:
        notes.append("no usable date column; trend section skipped")
    elif trend_rows is not None:
        points = [
            {"bucket": row["bucket"], "n": int(row["n"])}
            for row in trend_rows
            if row.get("bucket") is not None and row.get("n") is not None
        ]
        points.reverse()  # query is most-recent-first
        if len(trend_rows) >= TREND_MAX_POINTS:
            notes.append(
                f"trend truncated to the most recent {TREND_MAX_POINTS} buckets"
            )
        if points:
            trend = {
                "field": date_col["field_name"],
                "granularity": granularity,
                "points": points,
            }
            sections.append("trend")
        else:
            notes.append("trend query returned no rows; trend section skipped")

    categories = []
    for col in pick_category_columns(columns, row_count):
        non_null = col.get("non_null_count")
        values = [
            {"value": entry.get("value"), "count": entry.get("count")}
            for entry in col["top_values"]
        ]
        shown = sum(entry["count"] or 0 for entry in values)
        categories.append(
            {
                "field_name": col["field_name"],
                "distinct_count": col.get("distinct_count"),
                "null_rate": col.get("null_rate"),
                "values": values,
                "coverage": round(shown / non_null, 4) if non_null else None,
            }
        )
    if categories:
        sections.append("categories")

    numeric = numeric_summary(columns, row_count)
    if numeric:
        sections.append("numeric")

    quality = {
        "null_rates": sorted(
            (
                {
                    k: col.get(k)
                    for k in ("field_name", "type", "null_rate", "distinct_count")
                }
                for col in columns
            ),
            key=lambda c: -(c["null_rate"] or 0),
        ),
        "flags": find_landmines(columns, row_count),
        "profile_notes": list(profile.get("notes") or [])
        + [f"{c['field_name']}: {c['error']}" for c in columns if c.get("error")],
    }
    if quality["null_rates"]:
        sections.append("quality")

    if where:
        notes.append(
            "trend is filtered by `where`; profile-derived sections cover "
            "the full dataset"
        )

    date_span = None
    if date_col is not None:
        date_span = {
            "field": date_col["field_name"],
            "min": date_col.get("min"),
            "max": date_col.get("max"),
        }

    return {
        "title": title
        or metadata.get("name")
        or f"{metadata.get('domain')}/{metadata.get('id')}",
        "domain": metadata.get("domain"),
        "dataset_id": metadata.get("id"),
        "source_url": metadata.get("source_url"),
        "row_count": row_count,
        "update_frequency": metadata.get("update_frequency"),
        "license": metadata.get("license"),
        "attribution": metadata.get("attribution"),
        "data_updated_at": metadata.get("data_updated_at"),
        "generated_at": generated_at,
        "where": where,
        "date_span": date_span,
        "sections": sections,
        "trend": trend,
        "categories": categories,
        "numeric": numeric,
        "quality": quality,
        "queries": queries,
        "notes": notes,
        "version": version,
    }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: PASS (24 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/report.py tests/test_report.py
git commit -m "feat: build_report model assembly"
```

---

### Task 4: Formatting + SVG chart helpers (`report_html.py` part 1)

**Files:**
- Create: `socrata_mcp/report_html.py`
- Test: `tests/test_report_html.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_report_html.py`:

```python
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


class TestTicks:
    def test_covers_max_with_round_steps(self):
        ticks = _ticks(103051)
        assert ticks[0] == 0
        assert ticks[-1] >= 103051
        steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:])}
        assert len(steps) == 1  # uniform spacing

    def test_zero_max(self):
        assert _ticks(0) == [0.0]


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


class TestBarChart:
    def test_escapes_and_truncates_labels(self):
        svg = bar_chart_svg(
            [("<b>bold</b>", 10), ("x" * 60, 5)], aria_label="top values"
        )
        assert "<b>" not in svg
        assert "&lt;b&gt;" in svg
        assert "…" in svg
        ET.fromstring(svg)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_html.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'socrata_mcp.report_html'`

- [x] **Step 3: Write the implementation**

Create `socrata_mcp/report_html.py`:

```python
"""Self-contained HTML rendering for dataset reports.

Pure: model dict in, HTML string out. Stdlib only — f-string templates and
hand-generated SVG. No JavaScript, no external requests; the file must open
cleanly in a browser, an email preview, or a CI artifact store.
"""

from __future__ import annotations

import html
import math
from typing import Any

CHART_W = 720
MAX_BAR_LABEL_CHARS = 30


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _shorten(text: str, limit: int = MAX_BAR_LABEL_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact(n: float) -> str:
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suffix}".replace(".0", "")
    return f"{n:,.0f}"


def _ticks(max_value: float, count: int = 4) -> list[float]:
    """Uniform round-number ticks from 0, ending at the first tick >= max."""
    if max_value <= 0:
        return [0.0]
    raw = max_value / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for mult in (1, 2, 2.5, 5, 10):
        step = magnitude * mult
        if step * count >= max_value:
            break
    ticks = [step * i for i in range(count + 1)]
    top = next(i for i, tick in enumerate(ticks) if tick >= max_value)
    return ticks[: top + 1]


def _rounded_top(x: float, y_top: float, w: float, h: float) -> str:
    """Column with a 4px rounded data-end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4.0, h, w / 2)
    return (
        f'<path class="bar" d="M{x:.1f},{y_top + h:.1f} L{x:.1f},{y_top + r:.1f} '
        f"Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
        f"L{x + w - r:.1f},{y_top:.1f} "
        f"Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} "
        f'L{x + w:.1f},{y_top + h:.1f} Z"/>'
    )


def _rounded_right(x: float, y: float, w: float, h: float) -> str:
    """Horizontal bar with a 4px rounded data-end, square at the baseline."""
    if w <= 0:
        return ""
    r = min(4.0, w, h / 2)
    return (
        f'<path class="bar" d="M{x:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} '
        f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"L{x + w:.1f},{y + h - r:.1f} "
        f"Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} "
        f'L{x:.1f},{y + h:.1f} Z"/>'
    )


def column_chart_svg(points: list[tuple[str, float]], *, aria_label: str) -> str:
    """Vertical columns: (label, value) per point, first/peak/last labeled."""
    n = len(points)
    left, right, top, bottom, height = 56, 16, 20, 32, 280
    plot_w, plot_h = CHART_W - left - right, height - top - bottom
    ticks = _ticks(max(v for _, v in points))
    scale_max = ticks[-1] or 1

    def y(v: float) -> float:
        return top + plot_h * (1 - v / scale_max)

    parts = [
        f'<svg viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-label="{_esc(aria_label)}">'
    ]
    for tick in ticks:
        cls = "baseline" if tick == 0 else "gridline"
        parts.append(
            f'<line class="{cls}" x1="{left}" x2="{CHART_W - right}" '
            f'y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{_compact(tick)}</text>'
        )
    band = plot_w / n
    bar_w = min(24.0, band * 0.6)
    label_every = max(1, math.ceil(n / 12))
    peak_idx = max(range(n), key=lambda i: points[i][1])
    for i, (label, value) in enumerate(points):
        cx = left + band * i + band / 2
        parts.append(_rounded_top(cx - bar_w / 2, y(value), bar_w, y(0) - y(value)))
        if i % label_every == 0 or i == n - 1:
            parts.append(
                f'<text class="tick" x="{cx:.1f}" y="{height - 10}" '
                f'text-anchor="middle">{_esc(label)}</text>'
            )
        if i in (0, peak_idx, n - 1):
            parts.append(
                f'<text class="val" x="{cx:.1f}" y="{y(value) - 6:.1f}" '
                f'text-anchor="middle">{_compact(value)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart_svg(values: list[tuple[str, float]], *, aria_label: str) -> str:
    """Horizontal bars: (label, value) per row, value labels at bar tips."""
    left, right, top, bottom = 220, 64, 10, 6
    band, bar_h = 30, 18
    n = len(values)
    height = top + band * n + bottom
    plot_w = CHART_W - left - right
    peak = max((v for _, v in values), default=0) or 1
    parts = [
        f'<svg viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-label="{_esc(aria_label)}">'
    ]
    for i, (label, value) in enumerate(values):
        yy = top + band * i + (band - bar_h) / 2
        w = plot_w * value / peak
        parts.append(_rounded_right(left, yy, w, bar_h))
        parts.append(
            f'<text class="cat" x="{left - 10}" y="{yy + bar_h - 4:.1f}" '
            f'text-anchor="end">{_esc(_shorten(str(label)))}</text>'
        )
        parts.append(
            f'<text class="val" x="{left + w + 8:.1f}" y="{yy + bar_h - 4:.1f}">'
            f"{_compact(value)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report_html.py -v`
Expected: PASS (6 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/report_html.py tests/test_report_html.py
git commit -m "feat: SVG chart helpers for reports"
```

---

### Task 5: HTML renderer — `render_html` (`report_html.py` part 2)

**Files:**
- Modify: `socrata_mcp/report_html.py`
- Test: `tests/test_report_html.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_report_html.py`:

```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_html.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_html'`

- [x] **Step 3: Write the implementation**

Append to `socrata_mcp/report_html.py`:

```python
FLAG_LABELS = {
    "mostly_null": "Mostly null",
    "constant": "Constant value",
    "id_like": "One value per row",
    "case_variants": "Case-variant values",
}

_STYLE = """
  :root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#1a1a19; --ink-2:#52514e;
          --muted:#898781; --grid:#e1e0d9; --accent:#2a78d6;
          --border:rgba(11,11,11,.12); }
  @media (prefers-color-scheme: dark) {
    :root { --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
            --muted:#898781; --grid:#2c2c2a; --accent:#3987e5;
            --border:rgba(255,255,255,.12); }
  }
  body { margin:0; background:var(--page); color:var(--ink);
         font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
         font-size:15px; line-height:1.55; }
  main { max-width:820px; margin:0 auto; padding:40px 20px 64px; }
  h1 { font-size:26px; margin:0 0 6px; }
  h2 { font-size:19px; margin:36px 0 10px; }
  .meta { color:var(--muted); font-size:12.5px; margin:0 0 4px; }
  .meta a { color:var(--accent); }
  .card { background:var(--surface); border:1px solid var(--border);
          border-radius:10px; padding:16px 18px; margin:12px 0; }
  .note { color:var(--ink-2); font-size:13px; }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th { text-align:left; color:var(--ink-2); font-weight:600;
       border-bottom:1px solid var(--grid); padding:5px 14px 5px 0; }
  td { border-bottom:1px solid var(--grid); padding:5px 14px 5px 0;
       color:var(--ink-2); font-variant-numeric:tabular-nums; }
  svg { width:100%; height:auto; display:block; }
  svg .bar { fill:var(--accent); }
  svg .gridline { stroke:var(--grid); stroke-width:1; }
  svg .baseline { stroke:var(--muted); stroke-width:1; }
  svg text { font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  svg .tick { font-size:11px; fill:var(--muted); }
  svg .cat { font-size:12.5px; fill:var(--ink-2); }
  svg .val { font-size:11.5px; font-weight:600; fill:var(--ink-2); }
  footer { margin-top:40px; border-top:1px solid var(--grid);
           padding-top:14px; color:var(--muted); font-size:12.5px; }
  code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px;
         color:var(--ink-2); }
"""


def _pct(rate: Any) -> str:
    return "—" if rate is None else f"{rate:.1%}"


def _fmt_num(value: Any) -> str:
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return "—" if value is None else _esc(value)


def _header_html(model: dict[str, Any]) -> str:
    meta_bits = [
        f'<a href="{_esc(model["source_url"])}">'
        f'{_esc(model["domain"])}/{_esc(model["dataset_id"])}</a>'
    ]
    if model["row_count"] is not None:
        meta_bits.append(f"{model['row_count']:,} rows")
    span = model.get("date_span")
    if span:
        meta_bits.append(
            f"{_esc(str(span['min'])[:10])} → {_esc(str(span['max'])[:10])} "
            f"(<code>{_esc(span['field'])}</code>)"
        )
    if model["update_frequency"]:
        meta_bits.append(f"updated {_esc(model['update_frequency'])}")
    if model["license"]:
        meta_bits.append(_esc(model["license"]))
    lines = [
        f"<h1>{_esc(model['title'])}</h1>",
        f'<p class="meta">{" · ".join(meta_bits)}</p>',
    ]
    if model["attribution"]:
        lines.append(f'<p class="meta">Source: {_esc(model["attribution"])}</p>')
    if model["where"]:
        lines.append(
            f'<p class="meta">Filter: <code>{_esc(model["where"])}</code></p>'
        )
    return "\n".join(lines)


def _notes_html(notes: list[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{_esc(note)}</li>" for note in notes)
    return (
        '<div class="card note">'
        f'<ul style="margin:0;padding-left:18px">{items}</ul></div>'
    )


def _trend_html(model: dict[str, Any]) -> str:
    trend = model["trend"]
    width = 4 if trend["granularity"] == "year" else 7
    points = [(str(p["bucket"])[:width], p["n"]) for p in trend["points"]]
    svg = column_chart_svg(
        points, aria_label=f"Row count per {trend['granularity']}"
    )
    return (
        f'<section id="trend"><h2>Rows per {_esc(trend["granularity"])} — '
        f'<code>{_esc(trend["field"])}</code></h2>'
        f'<div class="card">{svg}</div></section>'
    )


def _categories_html(model: dict[str, Any]) -> str:
    blocks = []
    for cat in model["categories"]:
        values = [
            ("(null)" if v["value"] is None else str(v["value"]), v["count"] or 0)
            for v in cat["values"]
        ]
        svg = bar_chart_svg(
            values, aria_label=f"Top values of {cat['field_name']}"
        )
        coverage = ""
        if cat["coverage"] is not None and cat["coverage"] < 1:
            coverage = (
                f'<p class="note">Top {len(values)} values cover '
                f'{cat["coverage"]:.0%} of non-null rows.</p>'
            )
        blocks.append(
            f'<h2><code>{_esc(cat["field_name"])}</code> — '
            f'{cat["distinct_count"]} distinct</h2>'
            f'<div class="card">{svg}{coverage}</div>'
        )
    return f'<section id="categories">{"".join(blocks)}</section>'


def _numeric_html(model: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{_esc(c['field_name'])}</code></td>"
        f"<td>{_fmt_num(c.get('min'))}</td><td>{_fmt_num(c.get('max'))}</td>"
        f"<td>{_fmt_num(c.get('avg'))}</td><td>{_pct(c.get('null_rate'))}</td></tr>"
        for c in model["numeric"]
    )
    return (
        '<section id="numeric"><h2>Numeric columns</h2><div class="card">'
        "<table><thead><tr><th>Column</th><th>Min</th><th>Max</th><th>Avg</th>"
        f"<th>Null</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def _quality_html(model: dict[str, Any]) -> str:
    quality = model["quality"]
    parts = ['<section id="quality"><h2>Data quality</h2>']
    if quality["flags"]:
        rows = "".join(
            f"<tr><td><code>{_esc(f['field_name'])}</code></td>"
            f"<td>{_esc(FLAG_LABELS.get(f['flag'], f['flag']))}</td>"
            f"<td>{_esc(f['detail'])}</td></tr>"
            for f in quality["flags"]
        )
        parts.append(
            '<div class="card"><table><thead><tr><th>Column</th><th>Flag</th>'
            f"<th>Detail</th></tr></thead><tbody>{rows}</tbody></table></div>"
        )
    rows = "".join(
        f"<tr><td><code>{_esc(c['field_name'])}</code></td>"
        f"<td>{_esc(c.get('type') or '')}</td><td>{_pct(c.get('null_rate'))}</td>"
        f"<td>{c['distinct_count'] if c.get('distinct_count') is not None else '—'}"
        "</td></tr>"
        for c in quality["null_rates"]
    )
    parts.append(
        '<div class="card"><table><thead><tr><th>Column</th><th>Type</th>'
        f"<th>Null</th><th>Distinct</th></tr></thead><tbody>{rows}</tbody>"
        "</table></div>"
    )
    if quality["profile_notes"]:
        items = "".join(f"<li>{_esc(n)}</li>" for n in quality["profile_notes"])
        parts.append(f'<ul class="note">{items}</ul>')
    parts.append("</section>")
    return "".join(parts)


def _footer_html(model: dict[str, Any]) -> str:
    queries = "".join(f"<li><code>{_esc(q)}</code></li>" for q in model["queries"])
    query_block = f"<p>Queries run:</p><ul>{queries}</ul>" if queries else ""
    return (
        f"<footer>{query_block}"
        f"<p>Generated {_esc(model['generated_at'])} by socrata-mcp "
        f"{_esc(model['version'])}. Reflects the dataset as of generation "
        "time; aggregates computed portal-side.</p></footer>"
    )


_SECTION_RENDERERS = {
    "trend": _trend_html,
    "categories": _categories_html,
    "numeric": _numeric_html,
    "quality": _quality_html,
}


def render_html(model: dict[str, Any]) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(model['title'])}</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        _header_html(model),
        _notes_html(model["notes"]),
    ]
    for section in model["sections"]:
        parts.append(_SECTION_RENDERERS[section](model))
    parts.append(_footer_html(model))
    parts.append("</main></body></html>")
    return "\n".join(part for part in parts if part)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report_html.py -v`
Expected: PASS (10 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/report_html.py tests/test_report_html.py
git commit -m "feat: HTML report renderer"
```

---

### Task 6: Provider orchestration — `generate_report`

**Files:**
- Modify: `socrata_mcp/providers/base.py` (add abstract method after `export_csv`, line 100-108)
- Modify: `socrata_mcp/providers/socrata.py`
- Test: `tests/test_report.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_report.py`:

```python
from tests.conftest import DATASET, DOMAIN, VIEWS_PAYLOAD  # noqa: E402


def stub_profile_aggregates(fake_portal):
    """Aggregate + top-values stubs matching conftest's 3-column schema.

    offense_id: text, distinct == row_count (id-like); top values have a
    case-variant pair. offense_date: 2019->2026 span (year granularity).
    longitude: numeric with stats.
    """
    fake_portal.stub(
        lambda p: "count(distinct" in p.get("$select", ""),
        [
            {
                "nn_0": "250", "d_0": "250",
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v -k GenerateReport`
Expected: FAIL — `AttributeError: 'SocrataProvider' object has no attribute 'generate_report'`

- [x] **Step 3: Write the implementation**

In `socrata_mcp/providers/base.py`, append inside the `Provider` class after `export_csv`:

```python
    @abstractmethod
    def generate_report(
        self,
        domain: str,
        dataset_id: str,
        out_path: Path,
        where: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Write a self-contained HTML report -> {path, sections, notes, queries}."""
```

In `socrata_mcp/providers/socrata.py`, add to the imports:

```python
import importlib.metadata

from ..report import build_report, describe_query, pick_date_column, trend_spec
from ..report_html import render_html
```

(`datetime`, `timezone`, and `Path` are already imported.)

Then append this method to `SocrataProvider`:

```python
    def generate_report(
        self,
        domain: str,
        dataset_id: str,
        out_path: Path,
        where: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        metadata = self.get_dataset(domain, dataset_id)
        profile = self.profile_dataset(domain, dataset_id)

        date_col = pick_date_column(profile["columns"])
        trend_rows: list[dict[str, Any]] | None = None
        granularity: str | None = None
        queries: list[str] = []
        if date_col is not None:
            spec_args, granularity = trend_spec(date_col, where)
            result = self.query(domain, dataset_id, QuerySpec(**spec_args))
            trend_rows = result["rows"]
            queries.append(
                describe_query(
                    result["query"]["params"], result["query"]["effective_limit"]
                )
            )

        try:
            version = importlib.metadata.version("socrata-mcp")
        except importlib.metadata.PackageNotFoundError:
            version = "dev"
        model = build_report(
            metadata,
            profile,
            trend_rows=trend_rows,
            granularity=granularity,
            date_col=date_col,
            where=where,
            queries=queries,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            title=title,
            version=version,
        )
        html_doc = render_html(model)

        out = Path(out_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        # Temp-then-rename: a failed run never leaves a partial report.
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(html_doc, encoding="utf-8")
        tmp.replace(out)
        return {
            "path": str(out),
            "sections": model["sections"],
            "notes": model["notes"],
            "queries": model["queries"],
        }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: PASS (27 tests)

- [x] **Step 5: Run the whole suite (the ABC change affects other tests)**

Run: `pytest`
Expected: all tests PASS (`SocrataProvider` implements the new abstract method; no other `Provider` subclass exists)

- [x] **Step 6: Commit**

```bash
git add socrata_mcp/providers/base.py socrata_mcp/providers/socrata.py tests/test_report.py
git commit -m "feat: provider generate_report with temp-file write"
```

---

### Task 7: MCP tool surface

**Files:**
- Modify: `socrata_mcp/mcp/tools.py`
- Test: `tests/test_tools.py`

- [x] **Step 1: Write the failing tests**

In `tests/test_tools.py`, add `"report"` to `EXPECTED_TOOLS`:

```python
EXPECTED_TOOLS = {
    "search_datasets",
    "get_dataset",
    "query",
    "profile_dataset",
    "sample",
    "export_csv",
    "report",
}
```

Append at the end of the file:

```python
@pytest.mark.anyio
async def test_report_tool(mcp_provider, fake_portal, tmp_path):
    from tests.test_report import stub_profile_aggregates

    fake_portal.rows[DATASET] = [{"offense_id": str(i)} for i in range(250)]
    stub_profile_aggregates(fake_portal)
    fake_portal.stub(
        lambda p: "date_extract_y" in p.get("$select", ""),
        [{"bucket": "2026", "n": "100"}],
    )
    out = tmp_path / "report.html"
    async with client_session(server._mcp_server) as client:
        result = await client.call_tool(
            "report",
            {"domain": DOMAIN, "dataset_id": DATASET, "out_path": str(out)},
        )
        data = result_json(result)
        assert data["path"] == str(out)
        assert "quality" in data["sections"]
        assert out.exists()
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL — `test_all_tools_registered` (report missing) and `test_report_tool` (unknown tool)

- [x] **Step 3: Write the implementation**

Append to `socrata_mcp/mcp/tools.py`:

```python
@server.tool()
def report(
    domain: str,
    dataset_id: str,
    out_path: str,
    where: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Generate a self-contained HTML report for a dataset.

    Fully automatic: profiles the dataset, picks the primary date column and
    the most informative categorical columns, runs a trend query, and writes
    one dependency-free HTML file with charts, summary tables, and
    data-quality flags. Deterministic for a given dataset state — no model
    or human judgment in the loop.

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".
        out_path: Destination .html path (parent directories are created).
        where: Optional SoQL filter for the trend query, e.g.
            "offense_date >= '2025-01-01'". Profile-derived sections always
            cover the full dataset; the report notes this when set.
        title: Optional report title (defaults to the dataset name).

    Returns:
        {path, sections, notes, queries}. `sections` lists content sections
        rendered (from ["trend", "categories", "numeric", "quality"]);
        `queries` holds the SoQL executed, also shown in the report footer.
    """
    return get_provider().generate_report(
        domain, dataset_id, out_path, where=where, title=title
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: PASS (10 tests)

- [x] **Step 5: Commit**

```bash
git add socrata_mcp/mcp/tools.py tests/test_tools.py
git commit -m "feat: report MCP tool"
```

---

### Task 8: CLI entry point

**Files:**
- Create: `socrata_mcp/report_cli.py`
- Modify: `pyproject.toml` (the `[project.scripts]` table)
- Test: `tests/test_report_cli.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_report_cli.py`:

```python
from socrata_mcp import report_cli
from socrata_mcp.errors import PortalError


class StubProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def generate_report(self, domain, dataset_id, out_path, where=None, title=None):
        self.calls.append(
            {"domain": domain, "dataset_id": dataset_id, "out_path": out_path,
             "where": where, "title": title}
        )
        if self.error:
            raise self.error
        return self.result


def install(monkeypatch, stub):
    monkeypatch.setattr(report_cli, "_make_provider", lambda: stub)


def test_happy_path_prints_path_and_notes(monkeypatch, capsys, tmp_path):
    out = tmp_path / "r.html"
    stub = StubProvider(
        result={"path": str(out), "sections": ["quality"], "notes": ["n1"], "queries": []}
    )
    install(monkeypatch, stub)
    code = report_cli.main(
        ["data.example.gov", "abcd-1234", "-o", str(out),
         "--where", "a > 1", "--title", "T"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == str(out)
    assert "note: n1" in captured.err
    assert stub.calls[0]["where"] == "a > 1"
    assert stub.calls[0]["title"] == "T"


def test_default_out_path(monkeypatch, capsys):
    stub = StubProvider(
        result={"path": "abcd-1234-report.html", "sections": [], "notes": [], "queries": []}
    )
    install(monkeypatch, stub)
    assert report_cli.main(["data.example.gov", "abcd-1234"]) == 0
    assert stub.calls[0]["out_path"] == "abcd-1234-report.html"


def test_error_exits_nonzero(monkeypatch, capsys):
    install(monkeypatch, StubProvider(error=PortalError("no such view", status=404)))
    code = report_cli.main(["data.example.gov", "nope-0000"])
    assert code == 1
    assert "no such view" in capsys.readouterr().err
```

- [x] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'socrata_mcp.report_cli'`

- [x] **Step 3: Write the implementation**

Create `socrata_mcp/report_cli.py`:

```python
"""CLI: generate a dataset report with no MCP client or model in the loop."""

from __future__ import annotations

import argparse
import sys

from .cache import DiskCache
from .config import Config
from .errors import SocrataMCPError
from .http_client import HttpClient
from .providers.socrata import SocrataProvider


def _make_provider() -> SocrataProvider:
    config = Config.from_env()
    return SocrataProvider(
        config=config, http=HttpClient(config), cache=DiskCache(config.cache_dir)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="socrata-mcp-report",
        description="Generate a self-contained HTML report for a Socrata dataset.",
    )
    parser.add_argument("domain", help='portal hostname, e.g. "data.seattle.gov"')
    parser.add_argument("dataset_id", help='Socrata 4x4 id, e.g. "tazs-3rd5"')
    parser.add_argument(
        "-o", "--out", help="output path (default: ./<dataset_id>-report.html)"
    )
    parser.add_argument("--where", help="SoQL filter for the trend query")
    parser.add_argument("--title", help="report title (default: dataset name)")
    args = parser.parse_args(argv)

    out = args.out or f"{args.dataset_id}-report.html"
    try:
        result = _make_provider().generate_report(
            args.domain, args.dataset_id, out, where=args.where, title=args.title
        )
    except SocrataMCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for note in result["notes"]:
        print(f"note: {note}", file=sys.stderr)
    print(result["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `pyproject.toml`, extend `[project.scripts]`:

```toml
[project.scripts]
socrata-mcp = "socrata_mcp.server:main"
socrata-mcp-report = "socrata_mcp.report_cli:main"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report_cli.py -v`
Expected: PASS (3 tests)

- [x] **Step 5: Verify the console script resolves**

Run: `pip install -e . --quiet && socrata-mcp-report --help`
Expected: usage text listing `domain`, `dataset_id`, `-o`, `--where`, `--title`

- [x] **Step 6: Commit**

```bash
git add socrata_mcp/report_cli.py tests/test_report_cli.py pyproject.toml
git commit -m "feat: socrata-mcp-report CLI"
```

---

### Task 9: Live smoke test, README, full verification

**Files:**
- Modify: `tests/test_live_smoke.py`
- Modify: `README.md`

- [x] **Step 1: Add the live smoke test**

Append to `tests/test_live_smoke.py`:

```python
def test_report_generates_html(live_provider, tmp_path):
    out = tmp_path / "spd-report.html"
    result = live_provider.generate_report(DOMAIN, DATASET, out)
    assert out.exists()
    assert out.stat().st_size > 10_000
    assert "trend" in result["sections"]
    assert "quality" in result["sections"]
    text = out.read_text(encoding="utf-8")
    assert "<svg" in text
    assert "date_extract_y" in text or "date_trunc_ym" in text  # footer query
```

- [x] **Step 2: Update README**

In `README.md`, add `report` to the tool list (mirror the existing per-tool phrasing, after `export_csv`):

```markdown
- `report(domain, dataset_id, out_path, where?, title?)` — one-call HTML report:
  auto-detected trend chart, top-category charts, numeric summary, and
  data-quality flags. Self-contained file, no JS, no external requests.
  Also available without MCP: `socrata-mcp-report data.seattle.gov tazs-3rd5`.
```

Adjust wording to match the README's actual list style when editing.

- [x] **Step 3: Run the full offline suite**

Run: `pytest`
Expected: all tests PASS

- [x] **Step 4: Run the live smoke suite (network access required)**

Run: `pytest -m network --no-header -rN`
Expected: PASS, including `test_report_generates_html`. If the portal is
unreachable, note the failure and re-run later — do not mark this task
complete with a failing live test unless the failure is confirmed to be
network availability, not the code.

- [x] **Step 5: Generate a real report and eyeball it**

Run: `socrata-mcp-report data.seattle.gov tazs-3rd5 -o out/spd-report.html`
Expected: prints `out/spd-report.html`; open the file and confirm the header,
trend chart, category charts, and quality tables render sensibly in both
light and dark mode (macOS: toggle appearance, or use browser dev tools
`prefers-color-scheme` emulation).

- [x] **Step 6: Commit**

```bash
git add tests/test_live_smoke.py README.md
git commit -m "test: live smoke for report; docs: README report section"
```
