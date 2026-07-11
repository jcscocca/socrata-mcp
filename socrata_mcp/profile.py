"""Per-column dataset profiling via aggregate SoQL — never downloads the dataset.

One count(*) for the total, then chunked aggregate selects (null/distinct
counts, min/max/avg where the type allows), then a GROUP BY top-values query
per low-cardinality categorical column. Failures fall down a ladder instead
of retrying: chunk -> one column at a time -> that column without
count(distinct), the expensive aggregate on large datasets -> its portal
message recorded instead of aborting.

Above PROFILE_DISTINCT_MAX_ROWS rows, count(distinct) is skipped entirely:
one bounded GROUP BY probe per categorical column yields both the exact
cardinality (when <= TOP_VALUES_MAX_CARDINALITY) and the top values in a
single query; other columns report no distinct count on such datasets.
"""

from __future__ import annotations

from typing import Any, Callable

from .errors import PortalError

# The fallback ladder is the retry strategy: a timed-out aggregate is far
# more likely doomed than transient, so the transport loop gives up early.
PROFILE_MAX_ATTEMPTS = 2

# count(distinct) needs a full scan-and-hash per column; above this row
# count it routinely exceeds portal timeouts, so cardinality comes from
# bounded GROUP BY probes instead.
PROFILE_DISTINCT_MAX_ROWS = 5_000_000

CHUNK_SIZE = 8
TOP_VALUES_LIMIT = 10
TOP_VALUES_MAX_CARDINALITY = 500
MAX_PROFILE_COLUMNS = 50

NUMERIC_TYPES = {"number", "double", "money", "percent"}
DATE_TYPES = {"calendar_date", "date", "floating_timestamp", "fixed_timestamp"}
CATEGORICAL_TYPES = {"text", "checkbox"}

FetchJson = Callable[[dict[str, Any]], Any]


def _maybe_float(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _aggregate_parts(
    column: dict[str, Any], idx: int, include_distinct: bool = True
) -> list[str]:
    field, ctype = column["field_name"], column["type"]
    parts = [f"count({field}) as nn_{idx}"]
    if include_distinct:
        parts.append(f"count(distinct {field}) as d_{idx}")
    if ctype in NUMERIC_TYPES:
        parts += [
            f"min({field}) as mn_{idx}",
            f"max({field}) as mx_{idx}",
            f"avg({field}) as av_{idx}",
        ]
    elif ctype in DATE_TYPES:
        parts += [f"min({field}) as mn_{idx}", f"max({field}) as mx_{idx}"]
    return parts


def _apply_aggregates(
    stats: dict[str, Any], row: dict[str, Any], idx: int, ctype: str, total: int
) -> None:
    non_null = _maybe_int(row.get(f"nn_{idx}"))
    if non_null is not None:
        stats["non_null_count"] = non_null
        if total > 0:
            stats["null_rate"] = round(1 - non_null / total, 4)
    distinct = _maybe_int(row.get(f"d_{idx}"))
    if distinct is not None:
        stats["distinct_count"] = distinct
    if ctype in NUMERIC_TYPES:
        for key, alias in (("min", "mn"), ("max", "mx"), ("avg", "av")):
            if row.get(f"{alias}_{idx}") is not None:
                stats[key] = _maybe_float(row[f"{alias}_{idx}"])
    elif ctype in DATE_TYPES:
        for key, alias in (("min", "mn"), ("max", "mx")):
            if row.get(f"{alias}_{idx}") is not None:
                stats[key] = row[f"{alias}_{idx}"]


def profile_columns(
    fetch: FetchJson, columns: list[dict[str, Any]], total: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Profile columns via `fetch(params)` against a dataset's resource endpoint."""
    notes: list[str] = []
    eligible = [c for c in columns if c["field_name"] and not c["field_name"].startswith(":")]
    if len(eligible) > MAX_PROFILE_COLUMNS:
        notes.append(
            f"profiled first {MAX_PROFILE_COLUMNS} of {len(eligible)} columns"
        )
        eligible = eligible[:MAX_PROFILE_COLUMNS]

    exact_distinct = total <= PROFILE_DISTINCT_MAX_ROWS
    if not exact_distinct:
        notes.append(
            f"large dataset ({total:,} rows): distinct counts come from "
            "bounded group-by probes on categorical columns only"
        )

    indexed = list(enumerate(eligible))
    profiles: dict[str, dict[str, Any]] = {
        col["field_name"]: {"field_name": col["field_name"], "type": col["type"]}
        for _, col in indexed
    }

    def run_chunk(
        chunk: list[tuple[int, dict[str, Any]]], include_distinct: bool = True
    ) -> None:
        select = ", ".join(
            part
            for idx, col in chunk
            for part in _aggregate_parts(col, idx, include_distinct)
        )
        body = fetch({"$select": select})
        row = body[0] if body else {}
        for idx, col in chunk:
            _apply_aggregates(profiles[col["field_name"]], row, idx, col["type"], total)

    for start in range(0, len(indexed), CHUNK_SIZE):
        chunk = indexed[start : start + CHUNK_SIZE]
        try:
            run_chunk(chunk, include_distinct=exact_distinct)
        except PortalError:
            for item in chunk:
                try:
                    run_chunk([item], include_distinct=exact_distinct)
                except PortalError as exc:
                    field = item[1]["field_name"]
                    if not exact_distinct:
                        profiles[field]["error"] = exc.portal_message
                        continue
                    try:
                        run_chunk([item], include_distinct=False)
                    except PortalError:
                        profiles[field]["error"] = exc.portal_message
                    else:
                        profiles[field]["error"] = (
                            f"distinct count skipped: {exc.portal_message}"
                        )

    for _, col in indexed:
        stats = profiles[col["field_name"]]
        if col["type"] not in CATEGORICAL_TYPES:
            continue
        if exact_distinct:
            distinct = stats.get("distinct_count")
            if distinct is None or not 0 < distinct <= TOP_VALUES_MAX_CARDINALITY:
                continue
            limit = TOP_VALUES_LIMIT
        else:
            # One probe answers both cardinality and top values.
            limit = TOP_VALUES_MAX_CARDINALITY + 1
        field = col["field_name"]
        try:
            body = fetch(
                {
                    "$select": f"{field}, count(*) as count",
                    "$group": field,
                    "$order": "count DESC",
                    "$limit": str(limit),
                }
            )
        except PortalError as exc:
            stats["error"] = f"top values unavailable: {exc.portal_message}"
            continue
        if not exact_distinct:
            if len(body) > TOP_VALUES_MAX_CARDINALITY:
                continue  # cardinality above the cap stays unknown by design
            stats["distinct_count"] = len(body)
            if not body:
                continue
        stats["top_values"] = [
            {"value": row.get(field), "count": _maybe_int(row.get("count"))}
            for row in body[:TOP_VALUES_LIMIT]
        ]

    return [profiles[col["field_name"]] for _, col in indexed], notes
