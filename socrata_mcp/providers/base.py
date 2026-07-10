"""Provider-neutral vocabulary shared by the MCP tools and provider backends.

MVP ships Socrata only; a CKAN provider later implements the same surface
against these types without touching the tool layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WithinCircle:
    """Rows whose point-typed `field` falls within radius_m meters of (lat, lon)."""

    field: str
    lat: float
    lon: float
    radius_m: float


@dataclass(frozen=True)
class WithinBox:
    """Rows whose point-typed `field` falls inside the box (nw corner, se corner)."""

    field: str
    nw_lat: float
    nw_lon: float
    se_lat: float
    se_lon: float


@dataclass
class QuerySpec:
    """A dataset query: structured params OR a raw query string (`soql`), not both."""

    select: list[str] | None = None
    where: str | None = None
    group: list[str] | None = None
    order: str | None = None
    limit: int | None = None
    offset: int = 0
    soql: str | None = None
    within_circle: WithinCircle | None = None
    within_box: WithinBox | None = None

    def has_structured_parts(self) -> bool:
        return any(
            (
                self.select,
                self.where,
                self.group,
                self.order,
                self.limit is not None,
                self.offset,
                self.within_circle,
                self.within_box,
            )
        )


class Provider(ABC):
    """One open-data backend (Socrata now, CKAN later).

    All methods return plain JSON-serializable dicts; the MCP tool layer
    passes them through untouched.
    """

    @abstractmethod
    def search_datasets(
        self,
        query: str,
        domain: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Full-text catalog search -> {results, total, count, offset}."""

    @abstractmethod
    def get_dataset(self, domain: str, dataset_id: str) -> dict[str, Any]:
        """Dataset metadata: columns w/ types, row count, cadence, license."""

    @abstractmethod
    def query(self, domain: str, dataset_id: str, spec: QuerySpec) -> dict[str, Any]:
        """Run a validated, paged query -> {rows, row_count, truncated, query}."""

    @abstractmethod
    def profile_dataset(self, domain: str, dataset_id: str) -> dict[str, Any]:
        """Per-column stats computed portal-side via aggregate queries."""

    @abstractmethod
    def sample(self, domain: str, dataset_id: str, n: int = 10) -> dict[str, Any]:
        """First n rows -> {rows, row_count}."""

    @abstractmethod
    def export_csv(
        self,
        domain: str,
        dataset_id: str,
        spec: QuerySpec,
        out_path: Path,
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """Stream query results to a CSV file -> {path, rows_written, truncated}."""
