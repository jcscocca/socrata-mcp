# socrata-mcp MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An MCP server giving LLM agents typed, cached access to Socrata open-data portals (SODA 2.1 + Discovery API), with a thin provider interface so CKAN can be added later.

**Architecture:** Deterministic core (SoQL building, caching, HTTP, profiling, export) fully separated from the MCP layer, mirroring vizforge: `socrata_mcp/mcp/app.py` holds the FastMCP singleton with loud-failure logging; `socrata_mcp/mcp/tools.py` registers thin tool wrappers that delegate to a `Provider`. `SocrataProvider` implements the provider interface using an `HttpClient` (throttle + retry + portal-error surfacing) and a `DiskCache` (TTL, query-hash keyed, `~/.socrata-mcp/cache`). All HTTP goes through httpx so tests inject `httpx.MockTransport`.

**Tech Stack:** Python 3.11+, FastMCP (`mcp` SDK), httpx, pytest. No pandas — profiling is done portal-side via aggregate SoQL.

---

## File structure

```
socrata-mcp/
├── LICENSE                     # MIT
├── README.md
├── pyproject.toml              # setuptools; deps: mcp, httpx; dev: pytest
├── .gitignore
├── .mcp.json.example           # venv python -m socrata_mcp
├── docs/plans/                 # this plan
├── socrata_mcp/
│   ├── __init__.py             # __version__
│   ├── __main__.py             # python -m socrata_mcp → server.main()
│   ├── server.py               # main(): stdio transport
│   ├── config.py               # Config from env (token, cache dir, TTLs, caps, throttle)
│   ├── errors.py               # SocrataMCPError, PortalError (carries portal message/status/url)
│   ├── cache.py                # DiskCache: sha256-keyed JSON files + TTL, kind subdirs
│   ├── http_client.py          # HttpClient.get_json: throttle, retries w/ backoff, X-App-Token
│   ├── soql.py                 # QuerySpec → SODA params; validation; within_circle/within_box
│   ├── profile.py              # per-column profile via chunked aggregate SoQL
│   ├── export.py               # paged, streamed CSV writer (stable header from metadata)
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py             # Provider ABC + QuerySpec/dataclasses shared vocabulary
│   │   └── socrata.py          # SocrataProvider: Discovery search, views metadata, paged query
│   └── mcp/
│       ├── __init__.py
│       ├── app.py              # FastMCP singleton, exception logging (loud failures)
│       └── tools.py            # 6 tools; provider lookup; docstring-schemas for LLM
└── tests/
    ├── conftest.py             # FakePortal httpx.MockTransport + provider/client fixtures
    ├── test_cache.py
    ├── test_http_client.py
    ├── test_soql.py
    ├── test_provider.py        # search/metadata/query paging+truncation
    ├── test_profile.py
    ├── test_export.py
    ├── test_tools.py           # tool layer over mocked provider stack
    └── test_live_smoke.py      # @pytest.mark.network e2e vs data.seattle.gov
```

## Key design decisions

- **Provider interface** (`providers/base.py`): abstract `Provider` with the six tool-facing
  methods (`search_datasets`, `get_dataset`, `query`, `profile_dataset`, `sample`,
  `export_csv`). Tools never import Socrata specifics — CKAN later = one new module.
- **QuerySpec**: dataclass holding `select/where/group/order/limit/offset/soql/within_circle/within_box`.
  Structured params build `$select/$where/$group/$order`; raw `soql` is mutually exclusive
  with structured params and is sent as `$query` (validated: single statement, LIMIT capped).
- **Row cap + truncation**: query fetches pages of `page_size` (default 1000) up to
  `effective_limit = min(limit or default_limit, max_rows)`, requesting one extra row to set
  `truncated: true`. Defaults: `default_limit=100`, `max_rows=5000` (env-overridable).
  Deterministic paging: default `$order=:id` when caller gives no order (SODA offset paging
  is unstable without an order — proven pattern from the crime tool).
- **Profile via aggregates only**: one `count(*)`; chunked
  `count(col)/count(distinct col)/min/max` selects (~8 columns per request); per-categorical
  `GROUP BY col ORDER BY count DESC LIMIT 10` top values, only when distinct ≤ 500. Skip
  system columns (`:`-prefixed). If a chunk fails, retry per column; individual failures
  recorded as per-column `error`, never fatal.
- **Cache**: `DiskCache.get_or_fetch(kind, key_obj, ttl, fetch)`; key = sha256 of canonical
  JSON of the request; file `cache/<kind>/<hash>.json` with `{"cached_at": ..., "data": ...}`.
  TTLs: metadata/catalog 300s, query 3600s (env-overridable). Export streams and bypasses cache.
- **Politeness**: min interval between HTTP requests (default 0.2s, monotonic clock);
  retries on 429/5xx/connect errors with exponential backoff honoring `Retry-After`, max 4.
- **Loud failures**: portal JSON error bodies are parsed and raised as
  `PortalError(portal_message, status, url)`; the MCP layer logs and re-raises so FastMCP
  emits a real tool error (vizforge `_SafeFastMCP` pattern).
- **Export**: header = dataset metadata `fieldName`s (stable, Tableau/vizforge-ready even
  when JSON rows omit nulls); dict/list cells serialized as JSON strings; rows streamed
  page-by-page to disk; returns `{path, rows_written, truncated}`.

## Tasks

Each task is TDD: write failing tests → run (expect fail) → implement → run (expect pass) → commit.

- [x] **Task 1: Scaffolding** — pyproject (name `socrata-mcp`, package `socrata_mcp`,
  `requires-python >=3.11`, deps `mcp>=1.2`, `httpx>=0.27`; `[project.optional-dependencies] dev = pytest`),
  MIT LICENSE, .gitignore (venv, __pycache__, .pytest_cache, *.egg-info, out/), README stub,
  package skeleton with `__init__.py`s, `.mcp.json.example`, venv + editable install.
  Commit: `chore: scaffold socrata-mcp package`.
- [x] **Task 2: errors + config + cache** — tests: TTL expiry (monkeypatched time), key
  stability/canonicalization, kind separation, corrupt file treated as miss.
  Commit: `feat: config, errors, disk cache with TTL`.
- [x] **Task 3: http_client** — tests via MockTransport: app token header sent; 429 then 200
  retries (Retry-After honored, sleep monkeypatched); 400 with portal JSON body raises
  PortalError carrying portal message; throttle enforces min interval (fake clock).
  Commit: `feat: throttled, retrying HTTP client with portal error surfacing`.
- [x] **Task 4: soql** — tests: structured params → expected `$`-params; quoting/escaping;
  within_circle/within_box rendering and AND-merge with where; raw soql + structured →
  ValidationError; raw LIMIT > cap → error; raw without LIMIT gets capped LIMIT appended;
  semicolon rejection; limit clamping; default order `:id`.
  Commit: `feat: SoQL builder with validation and geo filters`.
- [x] **Task 5: provider base + SocrataProvider search/metadata/query** — FakePortal fixture;
  tests: Discovery API param mapping + result shaping; views metadata → DatasetInfo (columns
  w/ types, row count via count(*), license, update cadence fields); query pagination across
  3 pages; truncated flag; portal error propagation.
  Commit: `feat: Socrata provider — discovery search, metadata, paged query`.
- [x] **Task 6: profile** — FakePortal aggregate routing; tests: null rates, distinct counts,
  min/max for date/number cols, top values for low-cardinality text only, system columns
  skipped, chunk-failure → per-column fallback.
  Commit: `feat: server-side dataset profiling via aggregate SoQL`.
- [x] **Task 7: sample + export_csv** — tests: sample caps n; export writes stable header from
  metadata, streams multiple pages, serializes point dicts as JSON, truncation at max_export_rows,
  parent dirs created. Commit: `feat: sample and streamed CSV export`.
- [x] **Task 8: MCP layer** — app.py singleton + logging wrapper; tools.py registering
  search_datasets/get_dataset/query/profile_dataset/sample/export_csv; server.py main() +
  `__main__.py`; tests call tools through FastMCP in-process (list_tools + call_tool over
  the mocked provider). Commit: `feat: MCP server surface (FastMCP, stdio)`.
- [x] **Task 9: README + smoke tests** — README (what/why, install, .mcp.json, tool table,
  env vars, cache layout, vizforge chaining example); `test_live_smoke.py` marked
  `@pytest.mark.network` (excluded by default via `-m "not network"` in addopts): search
  data.seattle.gov, get_dataset tazs-3rd5, profile it, query last 30 days, export CSV.
  Commit: `docs: README; test: live smoke suite`.
- [x] **Task 10: Live verification** — run network suite for real; also drive the six tools
  end-to-end via an in-process MCP client against the live portal; fix whatever reality
  disagrees with (SoQL quirks, Discovery shapes); final commit.

## Verification gate

`pytest` green (mocked), `pytest -m network` green (live), CSV opens with sane rows,
each tool returns sane data against data.seattle.gov.
