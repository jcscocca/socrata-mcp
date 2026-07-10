"""MCP tool surface — thin wrappers that delegate to the active Provider."""

from __future__ import annotations

from typing import Any

from ..cache import DiskCache
from ..config import Config
from ..http_client import HttpClient
from ..providers.base import Provider, QuerySpec, WithinBox, WithinCircle
from ..providers.socrata import SocrataProvider
from .app import server

_provider: Provider | None = None


def set_provider(provider: Provider | None) -> None:
    """Inject a provider (tests / embedding); None resets to lazy default."""
    global _provider
    _provider = provider


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        config = Config.from_env()
        _provider = SocrataProvider(
            config=config, http=HttpClient(config), cache=DiskCache(config.cache_dir)
        )
    return _provider


@server.tool()
def search_datasets(
    query: str,
    domain: str | None = None,
    category: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Search open-data catalogs for datasets (Socrata Discovery API).

    Args:
        query: Full-text search, e.g. "crime reports" or "building permits".
        domain: Restrict to one portal, e.g. "data.seattle.gov".
        category: Portal category, e.g. "Public Safety".
        limit: Max results (default 20, cap 100).
        offset: Pagination offset into the result set.

    Returns:
        {results: [{id, name, domain, description, updated_at, category,
        permalink}], count, total, offset}. Use each result's domain + id
        with the other tools.
    """
    return get_provider().search_datasets(
        query, domain=domain, category=category, limit=limit, offset=offset
    )


@server.tool()
def get_dataset(domain: str, dataset_id: str) -> dict[str, Any]:
    """Dataset metadata: columns with types, row count, update cadence, license.

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".

    Returns:
        {name, description, columns: [{field_name, name, type, description}],
        row_count, license, attribution, created_at, data_updated_at,
        update_frequency, tags, source_url}. Use columns' field_name values
        in query/profile calls.
    """
    return get_provider().get_dataset(domain, dataset_id)


@server.tool()
def query(
    domain: str,
    dataset_id: str,
    select: list[str] | None = None,
    where: str | None = None,
    group: list[str] | None = None,
    order: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    soql: str | None = None,
    within_circle: WithinCircle | None = None,
    within_box: WithinBox | None = None,
) -> dict[str, Any]:
    """Query a dataset with structured SoQL parameters OR one raw SoQL string.

    Structured mode (recommended): pass any of select/where/group/order/
    limit/offset plus optional geo filters. Raw mode: pass `soql` only
    (e.g. "SELECT offense, count(*) GROUP BY offense LIMIT 50").

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".
        select: Columns/expressions, e.g. ["offense", "count(*) as n"].
        where: SoQL filter, e.g. "offense_date > '2026-06-01T00:00:00'".
        group: GROUP BY columns (pair with aggregate select expressions).
        order: e.g. "offense_date DESC". Defaults to ":id" for stable paging.
        limit: Max rows returned (default 100, hard cap applies).
        offset: Row offset for pagination.
        soql: Raw SoQL query — mutually exclusive with all structured params.
        within_circle: {field, lat, lon, radius_m} geo filter on a point column.
        within_box: {field, nw_lat, nw_lon, se_lat, se_lon} geo filter.

    Returns:
        {rows, row_count, truncated, query: {params, effective_limit, clamped}}.
        `truncated: true` means more rows matched than were returned — narrow
        the query or use export_csv for bulk extraction.
    """
    spec = QuerySpec(
        select=select,
        where=where,
        group=group,
        order=order,
        limit=limit,
        offset=offset,
        soql=soql,
        within_circle=within_circle,
        within_box=within_box,
    )
    return get_provider().query(domain, dataset_id, spec)


@server.tool()
def profile_dataset(domain: str, dataset_id: str) -> dict[str, Any]:
    """Profile every column: null rate, distinct count, min/max, top values.

    Computed portal-side via aggregate SoQL — the dataset is never downloaded.
    Dates and numbers get min/max (numbers also avg); low-cardinality text
    columns get their top 10 values with counts.

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".

    Returns:
        {row_count, columns: [{field_name, type, null_rate, non_null_count,
        distinct_count, min?, max?, avg?, top_values?, error?}], notes}.
    """
    return get_provider().profile_dataset(domain, dataset_id)


@server.tool()
def sample(domain: str, dataset_id: str, n: int = 10) -> dict[str, Any]:
    """Fetch the first n rows of a dataset (n capped at 100).

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".
        n: Number of rows (default 10, max 100).

    Returns:
        {rows, row_count, note}. Rows are in :id order — a peek at real
        values, not a random sample.
    """
    return get_provider().sample(domain, dataset_id, n=n)


@server.tool()
def export_csv(
    domain: str,
    dataset_id: str,
    out_path: str,
    select: list[str] | None = None,
    where: str | None = None,
    group: list[str] | None = None,
    order: str | None = None,
    limit: int | None = None,
    soql: str | None = None,
    within_circle: WithinCircle | None = None,
    within_box: WithinBox | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Export query results to a Tableau-ready CSV via streamed, paged download.

    Accepts the same query parameters as `query` (structured or raw `soql`)
    and writes matching rows to out_path. Designed to chain into vizforge's
    csv_to_dashboard. Point/location values are serialized as JSON strings.

    Args:
        domain: Portal hostname, e.g. "data.seattle.gov".
        dataset_id: Socrata 4x4 id, e.g. "tazs-3rd5".
        out_path: Destination .csv path (parent directories are created).
        select/where/group/order/limit/soql/within_circle/within_box: as in `query`.
        max_rows: Safety cap for this export (default 1,000,000).

    Returns:
        {path, rows_written, truncated, columns, notes}.
    """
    spec = QuerySpec(
        select=select,
        where=where,
        group=group,
        order=order,
        limit=limit,
        soql=soql,
        within_circle=within_circle,
        within_box=within_box,
    )
    return get_provider().export_csv(
        domain, dataset_id, spec, out_path, max_rows=max_rows
    )


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
