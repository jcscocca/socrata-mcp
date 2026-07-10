# Design: automatic dataset report tool

Date: 2026-07-10
Status: approved (design review in session)

## Goal

A repeatable, model-free way to turn any Socrata dataset into a readable
report: one call (MCP tool or CLI) produces a self-contained HTML file with
charts, summary tables, and data-quality flags. Deterministic: the same
dataset state yields the same report (footer timestamp aside).

## Surface

### MCP tool

```
report(domain, dataset_id, out_path, where=None, title=None)
  -> {path, sections, notes, queries}
```

- `where`: optional SoQL filter applied to every query the report runs
  (e.g. `"occ_date >= '2025-01-01'"`). The profile is unfiltered (portal
  aggregates over the full dataset); a note in the report says so when a
  `where` is active.
- `title`: overrides the dataset name in the report header.
- `sections`: content sections actually rendered, from
  `["trend", "categories", "numeric", "quality"]` — header and footer
  always render and are not listed.
- `queries`: the SoQL query strings executed, also rendered in the footer.
- `notes`: anything skipped or degraded (no date column, truncated trend,
  per-column profile errors).

### CLI

`socrata_mcp/report_cli.py` with `main()`, registered in
`[project.scripts]` as `socrata-mcp-report`:

```
socrata-mcp-report <domain> <dataset_id> [-o OUT] [--where WHERE] [--title TITLE]
```

Default out path: `./<dataset_id>-report.html`. Exit non-zero on error with
the portal message on stderr. No model, no MCP client needed — cron/CI safe.

## Data flow

```
generate_report (provider method)
  ├── get_dataset  (cached)        — metadata
  ├── profile_dataset (cached)     — per-column stats, top values
  ├── 1 trend query (existing query path)   — count over time
  ├── build_report(metadata, profile, trend, where)  -> model   [report.py]
  ├── render_html(model) -> str                       [report_html.py]
  └── write file, return result dict
```

The profile already carries top-10 values for low-cardinality text columns,
null rates, distincts, and min/max/avg — so the report costs metadata +
profile + one trend query.

## Report content

Sections, in order. A section that has nothing to show is omitted and noted.

1. **Header** — title, domain/dataset id (linked to source_url), row count,
   primary date span, update cadence, license/attribution, generated-at.
2. **Trend** — SVG column chart of `count(*)` grouped over the primary date
   column. Granularity: by year (`date_extract_y`) when the span ≥ 1095
   days, else by month (`date_trunc_ym`). Query limit 200 points; if more
   match, keep the most recent 200 and add a note.
3. **Top categories** — up to 3 horizontal-bar SVG charts, one per selected
   categorical column, drawn from the profile's `top_values` (no extra
   queries). Bars show value + count; a residual "everything else" line
   states coverage (sum of top-10 counts vs non-null count).
4. **Numeric summary** — table of min / max / avg for numeric columns,
   excluding id-like columns.
5. **Data quality** — null-rate table for all profiled columns (sorted
   descending), plus landmine flags (below). Profile column errors appear
   here verbatim.
6. **Footer** — the SoQL queries run, socrata-mcp version
   (`importlib.metadata`), cache note, disclaimer that report reflects the
   dataset as of generation time.

## Deterministic heuristics

All choices are pure functions of the metadata + profile, so the same
inputs always select the same columns.

**Primary date column.** Candidates: type in `DATE_TYPES` (reuse
`profile.py` constants) with both min and max present and `null_rate ≤ 0.5`.
Pick lowest `null_rate`; tie-break widest min→max span; then schema order.
No candidate → skip trend, add note.

**Categorical columns (top-categories section).** Candidates: type `text`,
`top_values` present, `2 ≤ distinct_count ≤ 50`, `null_rate ≤ 0.5`, and not
id-like. Rank: `null_rate` ascending, tie-break `distinct_count`
descending, then schema order. Take the first 3.

**Id-like column.** `distinct_count == row_count` and `row_count > 100`.
Excluded from categories and numeric summary; flagged informationally.

**Landmine flags** (each renders as a row in the quality section):

- *Mostly null*: `null_rate ≥ 0.5`.
- *Constant*: `distinct_count == 1`.
- *Id-like*: as above.
- *Case-variant values*: two `top_values` entries whose values are equal
  after `str.strip().lower()` but not equal raw (the `N` vs `n` case).

## Rendering

`report_html.py` — f-string templates, stdlib only. One `<style>` block,
CSS custom properties for the palette, dark mode via
`prefers-color-scheme`. No JavaScript, no external requests: the file is
fully self-contained and readable in any browser, email preview, or CI
artifact store.

SVG helper renders two chart shapes server-side:

- **Column chart** (trend): ≤ 24px-wide columns, hairline gridlines, clean
  y-ticks (round numbers, K/M-compact), rounded data-end caps, first/last/
  peak value labels only.
- **Horizontal bar chart** (categories): value labels at bar tips, category
  names as text (never color-encoded), single accent hue.

Charts use fixed viewBox widths (720) and `width: 100%` for responsiveness.
Colors: one accent hue + neutral grays, defined once as CSS variables.

## Error handling

- No usable date column → trend skipped, noted; report still renders.
- Zero-row dataset → header + schema + quality sections only, noted.
- Per-column profile errors → carried into the quality section (existing
  `profile.py` behavior), never fatal.
- Invalid `where` → the portal's 400 propagates as `PortalError` (existing
  path); the tool fails with the portal message, no partial file left
  behind (write to a temp file in the same directory, then rename).
- `out_path` parent directories created, as in `export_csv`.

## Testing

Mocked-fetch pattern from the existing suite (`conftest.py`):

- `test_report.py` — analyzer: fixture metadata/profile/trend → expected
  model; heuristics (date-column pick, category ranking, each landmine flag
  on a synthetic dirty profile); `where` propagation; skip/degrade paths.
- `test_report_html.py` — renderer: model → HTML contains expected section
  ids, SVG elements, escaped values (`html.escape` on all portal-sourced
  text); no assertions on the timestamp.
- `test_live_smoke.py` — one `network`-marked test: generate a report for
  the data.seattle.gov SPD dataset already used by the suite; assert file
  exists, non-trivial size, key sections present.
- CLI: happy path + error exit code with a mocked provider.

## Out of scope

- Geo/map sections, PDF output, multi-dataset reports.
- Model-supplied narrative sections (the rejected "hybrid" approach).
- Theming/branding parameters.
- Scheduling — cron owns repetition; the tool stays single-shot.
