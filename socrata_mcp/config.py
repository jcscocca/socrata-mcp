"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    app_token: str | None
    cache_dir: Path
    metadata_ttl: float
    catalog_ttl: float
    query_ttl: float
    default_limit: int
    max_rows: int
    max_export_rows: int
    page_size: int
    throttle_interval: float
    timeout: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        if env is None:
            env = os.environ
        token = env.get("SOCRATA_APP_TOKEN", "").strip() or None
        cache_dir = env.get("SOCRATA_MCP_CACHE_DIR", "").strip()
        return cls(
            app_token=token,
            cache_dir=Path(cache_dir) if cache_dir else Path.home() / ".socrata-mcp" / "cache",
            metadata_ttl=float(env.get("SOCRATA_MCP_METADATA_TTL", 300)),
            catalog_ttl=float(env.get("SOCRATA_MCP_CATALOG_TTL", 300)),
            query_ttl=float(env.get("SOCRATA_MCP_QUERY_TTL", 3600)),
            default_limit=int(env.get("SOCRATA_MCP_DEFAULT_LIMIT", 100)),
            max_rows=int(env.get("SOCRATA_MCP_MAX_ROWS", 5000)),
            max_export_rows=int(env.get("SOCRATA_MCP_MAX_EXPORT_ROWS", 1_000_000)),
            page_size=int(env.get("SOCRATA_MCP_PAGE_SIZE", 1000)),
            throttle_interval=float(env.get("SOCRATA_MCP_THROTTLE_INTERVAL", 0.2)),
            timeout=float(env.get("SOCRATA_MCP_TIMEOUT", 30)),
        )
