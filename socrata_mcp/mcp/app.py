"""FastMCP server singleton.

Import order matters: this module must be imported before tools.py so the
`server` instance exists when the @server.tool() decorators run.

Failures are loud (vizforge pattern): tool exceptions are logged with a
traceback and re-raised so FastMCP emits a real MCP error response carrying
the portal's actual message — never a fake success string.
"""

from __future__ import annotations

import functools
import logging

from mcp.server.fastmcp import FastMCP

_log = logging.getLogger(__name__)


class _LoudFastMCP(FastMCP):
    def tool(self, *args, **kwargs):
        decorator = super().tool(*args, **kwargs)

        def wrapping_decorator(func):
            @functools.wraps(func)
            def loud_wrapper(*a, **kw):
                try:
                    return func(*a, **kw)
                except Exception as exc:
                    _log.error("Tool %s failed: %s", func.__name__, exc, exc_info=True)
                    raise

            return decorator(loud_wrapper)

        return wrapping_decorator


server = _LoudFastMCP(
    "socrata",
    instructions=(
        "Typed, cached access to Socrata civic open-data portals "
        "(SODA 2.1 + Discovery API).\n\n"
        "Typical workflow:\n"
        "  1. search_datasets(query, domain?) — find datasets across portals\n"
        "  2. get_dataset(domain, dataset_id) — columns, types, row count, cadence\n"
        "  3. profile_dataset(domain, dataset_id) — null rates, distincts, "
        "min/max, top values (computed portal-side)\n"
        "  4. sample(domain, dataset_id, n) — peek at real rows\n"
        "  5. query(...) — structured SoQL (select/where/group/order/limit, "
        "within_circle/within_box) or raw soql; results are paged and "
        "row-capped with an explicit `truncated` flag\n"
        "  6. export_csv(...) — stream full results to a Tableau-ready CSV "
        "(chains into vizforge's csv_to_dashboard)\n\n"
        "Results are cached on disk (~/.socrata-mcp/cache) with short TTLs for "
        "metadata and configurable TTLs for query results. Set "
        "SOCRATA_APP_TOKEN to raise portal rate limits."
    ),
)
