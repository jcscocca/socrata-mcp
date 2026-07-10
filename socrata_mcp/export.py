"""Streamed, paged CSV export producing a Tableau-ready file.

Rows are written page-by-page — the full result set is never held in memory.
Header comes from dataset metadata for SELECT * exports (stable even though
SODA JSON omits null fields); projected queries derive it from the first page.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Iterator

from .soql import BuiltQuery

# A raw-SoQL export runs as a single request (paging would require rewriting
# the caller's query), so an explicit LIMIT is required beyond this default.
RAW_EXPORT_DEFAULT_LIMIT = 50_000

FetchJson = Callable[[dict[str, Any]], Any]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _pages(
    fetch: FetchJson, built: BuiltQuery, page_size: int, target: int
) -> Iterator[list[dict[str, Any]]]:
    if built.raw:
        yield fetch(built.params)
        return
    fetched = 0
    while fetched < target:
        page_limit = min(page_size, target - fetched)
        params = dict(built.params)
        params["$limit"] = str(page_limit)
        params["$offset"] = str(built.base_offset + fetched)
        page = fetch(params)
        yield page
        fetched += len(page)
        if len(page) < page_limit:
            break


def write_csv(
    fetch: FetchJson,
    built: BuiltQuery,
    out_path: Path,
    *,
    page_size: int,
    metadata_fieldnames: list[str] | None,
) -> dict[str, Any]:
    """Stream query pages into a CSV at out_path.

    metadata_fieldnames provides the header for SELECT * exports; pass None
    when the query projects columns (header then comes from the first page).
    """
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    effective = built.effective_limit
    rows_written = 0
    truncated = False
    dropped: set[str] = set()
    notes: list[str] = []
    fieldnames: list[str] | None = None
    writer: csv.DictWriter | None = None

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        for page in _pages(fetch, built, page_size, effective + 1):
            if writer is None:
                if metadata_fieldnames:
                    fieldnames = metadata_fieldnames
                else:
                    fieldnames = []
                    for row in page:
                        for key in row:
                            if key not in fieldnames:
                                fieldnames.append(key)
                writer = csv.DictWriter(
                    handle, fieldnames=fieldnames, extrasaction="ignore", restval=""
                )
                writer.writeheader()
            for row in page:
                if rows_written >= effective:
                    truncated = True
                    break
                dropped.update(k for k in row if k not in fieldnames)
                writer.writerow({k: _cell(v) for k, v in row.items()})
                rows_written += 1
            if truncated:
                break

    if dropped:
        notes.append(
            "columns not in the header were dropped: " + ", ".join(sorted(dropped))
        )
    if truncated:
        notes.append(f"export truncated at {effective} rows; more rows exist")
    return {
        "path": str(out_path),
        "rows_written": rows_written,
        "truncated": truncated,
        "columns": fieldnames or [],
        "notes": notes,
    }
