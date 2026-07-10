"""Provider-neutral vocabulary shared by the MCP tools and provider backends.

MVP ships Socrata only; a CKAN provider later implements the same surface
against these types without touching the tool layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
