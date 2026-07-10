# socrata-mcp

An MCP server that gives LLM agents typed, cached access to civic open-data
portals. Speaks Socrata (SODA 2.1 + Discovery API) today; the provider layer
is a thin interface so CKAN can be added later without touching the tool surface.

Highlights:

- **Server-side profiling** — null rates, distinct counts, min/max, top values
  computed via aggregate SoQL; the dataset is never downloaded.
- **Hard row caps with honest truncation** — every query result carries a
  `truncated` flag; paging uses a stable `:id` order.
- **Disk cache** under `~/.socrata-mcp/cache` keyed by query hash, with short
  TTLs for metadata and configurable TTLs for query results.
- **Polite by default** — request throttling, retries with backoff that honor
  `Retry-After`, optional `SOCRATA_APP_TOKEN` sent as `X-App-Token`.
- **Loud failures** — the portal's actual error message is surfaced to the
  agent, never swallowed.
- **Tableau-ready CSV export** — streamed, paged download designed to chain
  into [vizforge](https://github.com/jcscocca/vizforge)'s `csv_to_dashboard`.

## Install

```bash
git clone <this repo> && cd socrata-mcp
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Register with your MCP client (see `.mcp.json.example`):

```json
{
  "mcpServers": {
    "socrata": {
      "command": "/absolute/path/to/socrata-mcp/.venv/bin/python",
      "args": ["-m", "socrata_mcp"],
      "env": { "SOCRATA_APP_TOKEN": "optional-app-token" }
    }
  }
}
```

## Tools

| Tool | What it does |
| --- | --- |
| `search_datasets(query, domain?, category?, limit?, offset?)` | Full-text catalog search via the Socrata Discovery API. |
| `get_dataset(domain, dataset_id)` | Columns with types, row count, update cadence, license, attribution. |
| `query(domain, dataset_id, …)` | Structured SoQL (`select/where/group/order/limit/offset`, `within_circle`, `within_box`) **or** raw `soql`. Validated, paged, row-capped, `truncated` flag. |
| `profile_dataset(domain, dataset_id)` | Per-column null rates, distinct counts, min/max for dates/numbers, top values for categoricals — all portal-side. |
| `sample(domain, dataset_id, n?)` | First *n* rows (capped at 100) to see real values. |
| `export_csv(domain, dataset_id, out_path, …)` | Streamed, paged CSV export of any query. |

Example agent flow:

```
search_datasets("crime", domain="data.seattle.gov")
get_dataset("data.seattle.gov", "tazs-3rd5")
profile_dataset("data.seattle.gov", "tazs-3rd5")
query("data.seattle.gov", "tazs-3rd5",
      where="offense_date > '2026-06-10T00:00:00'",
      order="offense_date DESC", limit=100)
export_csv("data.seattle.gov", "tazs-3rd5", "out/spd_30d.csv",
           where="offense_date > '2026-06-10T00:00:00'")
# → vizforge: csv_to_dashboard("out/spd_30d.csv", ...)
```

## Configuration

All optional, via environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `SOCRATA_APP_TOKEN` | unset | Sent as `X-App-Token`; raises portal rate limits. |
| `SOCRATA_MCP_CACHE_DIR` | `~/.socrata-mcp/cache` | Disk cache root. |
| `SOCRATA_MCP_METADATA_TTL` | `300` | Seconds to cache dataset metadata. |
| `SOCRATA_MCP_CATALOG_TTL` | `300` | Seconds to cache catalog searches. |
| `SOCRATA_MCP_QUERY_TTL` | `3600` | Seconds to cache query/profile results (`0` disables). |
| `SOCRATA_MCP_DEFAULT_LIMIT` | `100` | Rows returned when a query gives no limit. |
| `SOCRATA_MCP_MAX_ROWS` | `5000` | Hard row cap for inline query results. |
| `SOCRATA_MCP_MAX_EXPORT_ROWS` | `1000000` | Hard row cap for CSV exports. |
| `SOCRATA_MCP_PAGE_SIZE` | `1000` | Rows fetched per HTTP request. |
| `SOCRATA_MCP_THROTTLE_INTERVAL` | `0.2` | Minimum seconds between portal requests. |
| `SOCRATA_MCP_TIMEOUT` | `30` | Per-request timeout in seconds. |

Cache layout: `cache/<kind>/<sha256>.json` (`kind` ∈ catalog, metadata, query,
profile), each file `{"cached_at": <epoch>, "data": …}`. Deleting the directory
is always safe.

Notes:

- Discovery searches use the US endpoint (`api.us.socrata.com`); EU-hosted
  portals are still directly queryable via `get_dataset`/`query` on their domain.
- Raw `soql` exports run as a single request, so give them an explicit `LIMIT`
  (default cap 50,000); structured exports page automatically.

## Development

```bash
.venv/bin/pytest              # unit tests (all HTTP mocked)
.venv/bin/pytest -m network   # live smoke tests against data.seattle.gov
```

Architecture: deterministic core (`soql.py`, `cache.py`, `http_client.py`,
`profile.py`, `export.py`) with the MCP layer (`socrata_mcp/mcp/`) as thin
wrappers over a `Provider` interface (`providers/base.py`). To add CKAN,
implement `Provider` in `providers/ckan.py` — the tool surface stays unchanged.

## License

MIT — see [LICENSE](LICENSE).
