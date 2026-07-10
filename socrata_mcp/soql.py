"""Build validated SODA query parameters from a QuerySpec.

Structured specs become $select/$where/$group/$order params; the provider adds
$limit/$offset per page. Raw SoQL is sent as a single $query request with its
LIMIT rewritten to effective_limit + 1 so truncation can be detected without
paging through a query string we'd otherwise have to rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError
from .providers.base import QuerySpec

_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)


def soql_quote(value: str) -> str:
    """Render a SoQL string literal (single quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


def _num(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass
class BuiltQuery:
    params: dict[str, str]
    effective_limit: int
    raw: bool
    base_offset: int = 0
    clamped: bool = False


def _geo_clauses(spec: QuerySpec) -> list[str]:
    clauses = []
    if spec.within_circle is not None:
        c = spec.within_circle
        clauses.append(
            f"within_circle({c.field}, {_num(c.lat)}, {_num(c.lon)}, {_num(c.radius_m)})"
        )
    if spec.within_box is not None:
        b = spec.within_box
        clauses.append(
            f"within_box({b.field}, {_num(b.nw_lat)}, {_num(b.nw_lon)}, "
            f"{_num(b.se_lat)}, {_num(b.se_lon)})"
        )
    return clauses


def _check_no_semicolons(*parts: str | None) -> None:
    for part in parts:
        if part and ";" in part:
            raise ValidationError(
                "Semicolons are not allowed in queries (single statement only)."
            )


def _build_raw(spec: QuerySpec, default_limit: int, max_rows: int) -> BuiltQuery:
    if spec.has_structured_parts():
        raise ValidationError(
            "Pass either raw `soql` or structured parameters "
            "(select/where/group/order/limit/offset/geo), not both."
        )
    _check_no_semicolons(spec.soql)
    text = spec.soql.strip()
    match = _LIMIT_RE.search(text)
    if match:
        declared = int(match.group(1))
        if declared > max_rows:
            raise ValidationError(
                f"Raw SoQL LIMIT {declared} exceeds the row cap of {max_rows}. "
                f"Lower the LIMIT or use export_csv for bulk extraction."
            )
        effective = declared
        # Rewrite LIMIT n -> LIMIT n+1: one extra row signals truncation.
        text = text[: match.start()] + f"LIMIT {declared + 1}" + text[match.end():]
    else:
        effective = default_limit
        text = f"{text} LIMIT {effective + 1}"
    return BuiltQuery(params={"$query": text}, effective_limit=effective, raw=True)


def build_query(spec: QuerySpec, *, default_limit: int, max_rows: int) -> BuiltQuery:
    if spec.soql:
        return _build_raw(spec, default_limit, max_rows)

    if spec.limit is not None and spec.limit < 0:
        raise ValidationError("limit must be >= 0")
    _check_no_semicolons(
        spec.where,
        spec.order,
        *(spec.select or []),
        *(spec.group or []),
    )

    params: dict[str, str] = {}
    if spec.select:
        params["$select"] = ", ".join(spec.select)
    if spec.group:
        params["$group"] = ", ".join(spec.group)

    where_parts = []
    if spec.where:
        where_parts.append(spec.where)
    where_parts.extend(_geo_clauses(spec))
    if len(where_parts) == 1:
        params["$where"] = where_parts[0]
    elif where_parts:
        params["$where"] = " AND ".join(f"({part})" for part in where_parts)

    if spec.order:
        params["$order"] = spec.order
    elif spec.group:
        # :id is invalid under GROUP BY; group columns give deterministic paging.
        params["$order"] = ", ".join(spec.group)
    else:
        params["$order"] = ":id"

    requested = spec.limit if spec.limit is not None else default_limit
    effective = min(requested, max_rows)
    return BuiltQuery(
        params=params,
        effective_limit=effective,
        raw=False,
        base_offset=spec.offset,
        clamped=requested > max_rows,
    )
