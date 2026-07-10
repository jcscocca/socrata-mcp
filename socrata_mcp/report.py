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
