"""Socrata backend: Discovery API catalog search + SODA 2.1 dataset access."""

from __future__ import annotations

import importlib.metadata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..cache import DiskCache
from ..config import Config
from ..errors import PortalError
from ..export import RAW_EXPORT_DEFAULT_LIMIT, write_csv
from ..http_client import HttpClient
from ..profile import profile_columns
from ..report import build_report, describe_query, pick_date_column, trend_spec
from ..report_html import render_html
from ..soql import BuiltQuery, build_query
from .base import Provider, QuerySpec

DISCOVERY_URL = "https://api.us.socrata.com/api/catalog/v1"
DESCRIPTION_LIMIT = 300
SAMPLE_MAX = 100


def _iso(epoch: Any) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _truncate(text: str | None, limit: int = DESCRIPTION_LIMIT) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "..."


def _find_update_frequency(custom_fields: Any) -> str | None:
    """Portals stash update cadence in metadata.custom_fields under varying names."""
    if isinstance(custom_fields, dict):
        for key, value in custom_fields.items():
            if "freq" in key.lower() and isinstance(value, str):
                return value
            found = _find_update_frequency(value)
            if found:
                return found
    return None


class SocrataProvider(Provider):
    def __init__(self, config: Config, http: HttpClient, cache: DiskCache):
        self.config = config
        self.http = http
        self.cache = cache

    def _resource_url(self, domain: str, dataset_id: str) -> str:
        return f"https://{domain}/resource/{dataset_id}.json"

    # ------------------------------------------------------------------
    # search_datasets
    # ------------------------------------------------------------------

    def search_datasets(
        self,
        query: str,
        domain: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "q": query,
            "only": "dataset",
            "limit": min(limit, 100),
            "offset": offset,
        }
        if domain:
            # `domains` alone yields 0 hits for full-text searches on federated
            # portals (observed on data.seattle.gov); search_context fixes it.
            params["domains"] = domain
            params["search_context"] = domain
        if category:
            params["categories"] = category

        raw, _ = self.cache.get_or_fetch(
            "catalog",
            {"url": DISCOVERY_URL, "params": params},
            ttl=self.config.catalog_ttl,
            fetch=lambda: self.http.get_json(DISCOVERY_URL, params),
        )
        results = [
            {
                "id": entry.get("resource", {}).get("id"),
                "name": entry.get("resource", {}).get("name"),
                "domain": entry.get("metadata", {}).get("domain"),
                "description": _truncate(entry.get("resource", {}).get("description")),
                "updated_at": entry.get("resource", {}).get("updatedAt"),
                "category": entry.get("classification", {}).get("domain_category"),
                "permalink": entry.get("permalink"),
            }
            for entry in raw.get("results", [])
        ]
        return {
            "results": results,
            "count": len(results),
            "total": raw.get("resultSetSize"),
            "offset": offset,
        }

    # ------------------------------------------------------------------
    # get_dataset
    # ------------------------------------------------------------------

    def get_dataset(self, domain: str, dataset_id: str) -> dict[str, Any]:
        views_url = f"https://{domain}/api/views/{dataset_id}.json"
        meta, _ = self.cache.get_or_fetch(
            "metadata",
            {"url": views_url},
            ttl=self.config.metadata_ttl,
            fetch=lambda: self.http.get_json(views_url),
        )

        notes: list[str] = []
        row_count: int | None = None

        def fetch_count() -> int:
            body = self.http.get_json(
                self._resource_url(domain, dataset_id), {"$select": "count(*) as count"}
            )
            return int(body[0]["count"])

        try:
            row_count, _ = self.cache.get_or_fetch(
                "metadata",
                {"count_for": f"{domain}/{dataset_id}"},
                ttl=self.config.metadata_ttl,
                fetch=fetch_count,
            )
        except PortalError as exc:
            notes.append(f"row count unavailable: {exc.portal_message}")

        license_info = meta.get("license") or {}
        columns = [
            {
                "field_name": col.get("fieldName"),
                "name": col.get("name"),
                "type": col.get("dataTypeName"),
                "description": col.get("description"),
            }
            for col in meta.get("columns", [])
        ]
        return {
            "id": dataset_id,
            "domain": domain,
            "name": meta.get("name"),
            "description": meta.get("description"),
            "category": meta.get("category"),
            "tags": meta.get("tags", []),
            "row_count": row_count,
            "columns": columns,
            "license": license_info.get("name") or meta.get("licenseId"),
            "attribution": meta.get("attribution"),
            "created_at": _iso(meta.get("createdAt")),
            "data_updated_at": _iso(meta.get("rowsUpdatedAt")),
            "metadata_updated_at": _iso(meta.get("viewLastModified")),
            "update_frequency": _find_update_frequency(
                (meta.get("metadata") or {}).get("custom_fields")
            ),
            "source_url": f"https://{domain}/d/{dataset_id}",
            "notes": notes,
        }

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def _fetch_rows(
        self, domain: str, dataset_id: str, built: BuiltQuery
    ) -> list[dict[str, Any]]:
        """Fetch up to effective_limit + 1 rows (the extra row flags truncation)."""
        url = self._resource_url(domain, dataset_id)
        if built.raw:
            return self.http.get_json(url, built.params)
        target = built.effective_limit + 1
        rows: list[dict[str, Any]] = []
        while len(rows) < target:
            page_limit = min(self.config.page_size, target - len(rows))
            params = dict(built.params)
            params["$limit"] = str(page_limit)
            params["$offset"] = str(built.base_offset + len(rows))
            page = self.http.get_json(url, params)
            rows.extend(page)
            if len(page) < page_limit:
                break
        return rows

    def query(
        self,
        domain: str,
        dataset_id: str,
        spec: QuerySpec,
        *,
        cache_salt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        built = build_query(
            spec, default_limit=self.config.default_limit, max_rows=self.config.max_rows
        )
        cache_key = {
            "domain": domain,
            "dataset_id": dataset_id,
            "params": built.params,
            "effective_limit": built.effective_limit,
            "base_offset": built.base_offset,
        }
        if cache_salt:
            cache_key["salt"] = cache_salt
        rows, _ = self.cache.get_or_fetch(
            "query",
            cache_key,
            ttl=self.config.query_ttl,
            fetch=lambda: self._fetch_rows(domain, dataset_id, built),
        )
        truncated = len(rows) > built.effective_limit
        rows = rows[: built.effective_limit]
        return {
            "domain": domain,
            "dataset_id": dataset_id,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "query": {
                "params": built.params,
                "effective_limit": built.effective_limit,
                "clamped": built.clamped,
            },
        }

    # ------------------------------------------------------------------
    # implemented in later tasks
    # ------------------------------------------------------------------

    def profile_dataset(self, domain: str, dataset_id: str) -> dict[str, Any]:
        info = self.get_dataset(domain, dataset_id)
        total = info["row_count"]
        if total is None:
            raise PortalError(
                "cannot profile: row count unavailable for this dataset",
                url=self._resource_url(domain, dataset_id),
            )

        def compute() -> dict[str, Any]:
            url = self._resource_url(domain, dataset_id)
            columns, notes = profile_columns(
                lambda params: self.http.get_json(url, params), info["columns"], total
            )
            return {
                "domain": domain,
                "dataset_id": dataset_id,
                "row_count": total,
                "columns": columns,
                "notes": notes,
            }

        result, _ = self.cache.get_or_fetch(
            "profile",
            {"domain": domain, "dataset_id": dataset_id, "row_count": total},
            ttl=self.config.query_ttl,
            fetch=compute,
        )
        return result

    def sample(self, domain: str, dataset_id: str, n: int = 10) -> dict[str, Any]:
        capped = max(1, min(n, SAMPLE_MAX))
        result = self.query(domain, dataset_id, QuerySpec(limit=capped))
        note = "first rows in :id order, not a random sample"
        if n > SAMPLE_MAX:
            note += f"; n capped at {SAMPLE_MAX}"
        return {
            "domain": domain,
            "dataset_id": dataset_id,
            "rows": result["rows"],
            "row_count": result["row_count"],
            "note": note,
        }

    def export_csv(
        self,
        domain: str,
        dataset_id: str,
        spec: QuerySpec,
        out_path: Path,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        cap = min(max_rows or self.config.max_export_rows, self.config.max_export_rows)
        default_limit = min(RAW_EXPORT_DEFAULT_LIMIT, cap) if spec.soql else cap
        built = build_query(spec, default_limit=default_limit, max_rows=cap)

        projected = built.raw or spec.select or spec.group
        metadata_fieldnames = None
        if not projected:
            info = self.get_dataset(domain, dataset_id)
            metadata_fieldnames = [
                c["field_name"]
                for c in info["columns"]
                if c["field_name"] and not c["field_name"].startswith(":")
            ]

        url = self._resource_url(domain, dataset_id)
        result = write_csv(
            lambda params: self.http.get_json(url, params),
            built,
            Path(out_path),
            page_size=self.config.page_size,
            metadata_fieldnames=metadata_fieldnames,
        )
        result["domain"] = domain
        result["dataset_id"] = dataset_id
        return result

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
        trend_truncated = False
        queries: list[str] = []
        if date_col is not None:
            spec_args, granularity = trend_spec(date_col, where)
            # Salt the query cache with dataset freshness so a regenerated
            # report can't pair a fresh profile with an hour-stale trend.
            result = self.query(
                domain,
                dataset_id,
                QuerySpec(**spec_args),
                cache_salt={"data_updated_at": metadata.get("data_updated_at")},
            )
            trend_rows = result["rows"]
            trend_truncated = result["truncated"]
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
            trend_truncated=trend_truncated,
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
