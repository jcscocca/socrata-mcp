"""Self-contained HTML rendering for dataset reports.

Pure: model dict in, HTML string out. Stdlib only — f-string templates and
hand-generated SVG. No JavaScript, no external requests; the file must open
cleanly in a browser, an email preview, or a CI artifact store.
"""

from __future__ import annotations

import html
import math
from typing import Any

CHART_W = 720
MAX_BAR_LABEL_CHARS = 30


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _shorten(text: str, limit: int = MAX_BAR_LABEL_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact(n: float) -> str:
    n = float(n)
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        value = round(n / div, 1)
        if abs(value) >= 1:
            return f"{value:.1f}".removesuffix(".0") + suffix
    return f"{n:,.0f}"


def _ticks(max_value: float, count: int = 4) -> list[float]:
    """Uniform integer ticks from 0, ending at the first tick >= max."""
    if max_value <= 0:
        return [0.0]
    raw = max_value / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for mult in (1, 2, 2.5, 5, 10):
        step = magnitude * mult
        if step * count >= max_value:
            break
    step = max(step, 1.0)  # axis carries counts; sub-integer ticks read as noise
    ticks = [step * i for i in range(count + 1)]
    top = next(i for i, tick in enumerate(ticks) if tick >= max_value)
    return ticks[: top + 1]


def _bar_path(d: str, cls: str, tooltip: str | None) -> str:
    """A bar path, optionally carrying a native browser tooltip (no JS)."""
    if tooltip:
        return f'<path class="{cls}" d="{d}"><title>{_esc(tooltip)}</title></path>'
    return f'<path class="{cls}" d="{d}"/>'


def _rounded_top(
    x: float, y_top: float, w: float, h: float,
    cls: str = "bar", tooltip: str | None = None,
) -> str:
    """Column with a 4px rounded data-end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4.0, h, w / 2)
    d = (
        f"M{x:.1f},{y_top + h:.1f} L{x:.1f},{y_top + r:.1f} "
        f"Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
        f"L{x + w - r:.1f},{y_top:.1f} "
        f"Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} "
        f"L{x + w:.1f},{y_top + h:.1f} Z"
    )
    return _bar_path(d, cls, tooltip)


def _rounded_right(
    x: float, y: float, w: float, h: float,
    cls: str = "bar", tooltip: str | None = None,
) -> str:
    """Horizontal bar with a 4px rounded data-end, square at the baseline."""
    if w <= 0:
        return ""
    r = min(4.0, w, h / 2)
    d = (
        f"M{x:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} "
        f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"L{x + w:.1f},{y + h - r:.1f} "
        f"Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} "
        f"L{x:.1f},{y + h:.1f} Z"
    )
    return _bar_path(d, cls, tooltip)


def column_chart_svg(
    points: list[tuple[str, float]], *, aria_label: str, partial_last: bool = False
) -> str:
    """Vertical columns: (label, value) per point, first/peak/last labeled."""
    n = len(points)
    left, right, top, bottom, height = 56, 16, 20, 32, 280
    plot_w, plot_h = CHART_W - left - right, height - top - bottom
    ticks = _ticks(max((v for _, v in points), default=0))
    scale_max = ticks[-1] or 1

    def y(v: float) -> float:
        return top + plot_h * (1 - v / scale_max)

    parts = [
        f'<svg viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-label="{_esc(aria_label)}">'
    ]
    for tick in ticks:
        cls = "baseline" if tick == 0 else "gridline"
        parts.append(
            f'<line class="{cls}" x1="{left}" x2="{CHART_W - right}" '
            f'y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{y(tick) + 4:.1f}" '
            f'text-anchor="end">{_compact(tick)}</text>'
        )
    band = plot_w / (n or 1)
    bar_w = min(24.0, band * 0.6)
    label_every = max(1, math.ceil(n / 12))
    peak_idx = max(range(n), key=lambda i: points[i][1], default=0)
    for i, (label, value) in enumerate(points):
        cx = left + band * i + band / 2
        is_partial = partial_last and i == n - 1
        tooltip = f"{label} — {value:,.0f} rows" + (" (partial)" if is_partial else "")
        parts.append(
            _rounded_top(
                cx - bar_w / 2, y(value), bar_w, y(0) - y(value),
                cls="bar partial" if is_partial else "bar",
                tooltip=tooltip,
            )
        )
        if i % label_every == 0 or i == n - 1:
            parts.append(
                f'<text class="tick" x="{cx:.1f}" y="{height - 10}" '
                f'text-anchor="middle">{_esc(label)}</text>'
            )
        if i in (0, peak_idx, n - 1):
            parts.append(
                f'<text class="val" x="{cx:.1f}" y="{y(value) - 6:.1f}" '
                f'text-anchor="middle">{_compact(value)}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def bar_chart_svg(values: list[tuple[str, float]], *, aria_label: str) -> str:
    """Horizontal bars: (label, value) per row, value labels at bar tips."""
    left, right, top, bottom = 220, 64, 10, 6
    band, bar_h = 30, 18
    n = len(values)
    height = top + band * n + bottom
    plot_w = CHART_W - left - right
    peak = max((v for _, v in values), default=0) or 1
    parts = [
        f'<svg viewBox="0 0 {CHART_W} {height}" role="img" '
        f'aria-label="{_esc(aria_label)}">'
    ]
    for i, (label, value) in enumerate(values):
        yy = top + band * i + (band - bar_h) / 2
        w = plot_w * value / peak
        parts.append(
            _rounded_right(
                left, yy, w, bar_h, tooltip=f"{label} — {value:,.0f} rows"
            )
        )
        parts.append(
            f'<text class="cat" x="{left - 10}" y="{yy + bar_h - 4:.1f}" '
            f'text-anchor="end">{_esc(_shorten(str(label)))}</text>'
        )
        parts.append(
            f'<text class="val" x="{left + w + 8:.1f}" y="{yy + bar_h - 4:.1f}">'
            f"{_compact(value)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


FLAG_LABELS = {
    "mostly_null": "Mostly null",
    "constant": "Constant value",
    "id_like": "One value per row",
    "case_variants": "Case-variant values",
    "date_artifacts": "Date artifacts",
}

FLAG_IMPACTS = {
    "mostly_null": "Analyses assuming this column is populated silently drop most rows.",
    "constant": "Carries no information; filtering or grouping on it is a no-op.",
    "id_like": "An identifier, not a category — exclude it from grouping and distinct-value analysis.",
    "case_variants": "Case-sensitive filters and group-bys will split or miss these values.",
    "date_artifacts": "Raw min/max dates are unreliable; use the effective span shown above.",
}

_STYLE = """
  :root { --page:#f9f9f7; --surface:#fcfcfb; --ink:#1a1a19; --ink-2:#52514e;
          --muted:#898781; --grid:#e1e0d9; --accent:#2a78d6;
          --border:rgba(11,11,11,.12); }
  @media (prefers-color-scheme: dark) {
    :root { --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink-2:#c3c2b7;
            --muted:#898781; --grid:#2c2c2a; --accent:#3987e5;
            --border:rgba(255,255,255,.12); }
  }
  body { margin:0; background:var(--page); color:var(--ink);
         font-family:"Avenir Next",Avenir,"Segoe UI Variable Text","Segoe UI",
                     system-ui,-apple-system,sans-serif;
         font-size:15px; line-height:1.55; }
  main { max-width:820px; margin:0 auto; padding:44px 20px 64px; }
  .eyebrow { font-family:ui-monospace,Menlo,Consolas,monospace;
             font-size:11px; letter-spacing:.14em; text-transform:uppercase;
             color:var(--accent); margin:0 0 8px; }
  h1 { font-size:28px; letter-spacing:-.01em; margin:0 0 6px; }
  h2 { font-size:19px; margin:36px 0 10px; }
  .meta { color:var(--muted); font-size:12.5px; margin:0 0 4px; }
  .meta a { color:var(--accent); }
  .card { background:var(--surface); border:1px solid var(--border);
          border-radius:10px; padding:16px 18px; margin:12px 0; }
  .note { color:var(--ink-2); font-size:13px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin:20px 0 4px; }
  .tile { background:var(--surface); border:1px solid var(--border);
          border-radius:8px; padding:12px 14px; display:flex;
          flex-direction:column; gap:2px; }
  .tile .label { font-size:12px; color:var(--ink-2); }
  .tile .value { font-size:24px; font-weight:600; line-height:1.2; }
  .tile .sub { font-size:11.5px; color:var(--muted); }
  table { border-collapse:collapse; width:100%; font-size:13.5px; }
  th { text-align:left; color:var(--ink-2); font-weight:600;
       border-bottom:1px solid var(--grid); padding:5px 14px 5px 0; }
  td { border-bottom:1px solid var(--grid); padding:5px 14px 5px 0;
       color:var(--ink-2); font-variant-numeric:tabular-nums; }
  svg { width:100%; height:auto; display:block; }
  svg .bar { fill:var(--accent); }
  svg .bar.partial { opacity:.45; }
  svg .gridline { stroke:var(--grid); stroke-width:1; }
  svg .baseline { stroke:var(--muted); stroke-width:1; }
  svg text { font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  svg .tick { font-size:11px; fill:var(--muted); }
  svg .cat { font-size:12.5px; fill:var(--ink-2); }
  svg .val { font-size:11.5px; font-weight:600; fill:var(--ink-2); }
  footer { margin-top:40px; border-top:1px solid var(--grid);
           padding-top:14px; color:var(--muted); font-size:12.5px; }
  code { font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px;
         color:var(--ink-2); }
"""


def _pct(rate: Any) -> str:
    return "—" if rate is None else f"{rate:.1%}"


def _fmt_num(value: Any) -> str:
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return "—" if value is None else _esc(value)


def _header_html(model: dict[str, Any]) -> str:
    meta_bits = [
        f'<a href="{_esc(model["source_url"])}">'
        f'{_esc(model["domain"])}/{_esc(model["dataset_id"])}</a>'
    ]
    if model["row_count"] is not None:
        meta_bits.append(f"{model['row_count']:,} rows")
    span = model.get("date_span")
    if span:
        lo = span.get("effective_min") or str(span["min"])[:10]
        hi = span.get("effective_max") or str(span["max"])[:10]
        meta_bits.append(
            f"{_esc(lo)} → {_esc(hi)} (<code>{_esc(span['field'])}</code>)"
        )
    if model["update_frequency"]:
        meta_bits.append(f"updated {_esc(model['update_frequency'])}")
    if model["data_updated_at"]:
        meta_bits.append(f"data updated {_esc(str(model['data_updated_at'])[:10])}")
    if model["license"]:
        meta_bits.append(_esc(model["license"]))
    lines = [
        '<p class="eyebrow">socrata-mcp · dataset report</p>',
        f"<h1>{_esc(model['title'])}</h1>",
        f'<p class="meta">{" · ".join(meta_bits)}</p>',
    ]
    if model["attribution"]:
        lines.append(f'<p class="meta">Source: {_esc(model["attribution"])}</p>')
    if model["where"] and model.get("trend"):
        lines.append(
            f'<p class="meta">Filter: <code>{_esc(model["where"])}</code></p>'
        )
    return "\n".join(lines)


def _tiles_html(model: dict[str, Any]) -> str:
    """Headline stat tiles; deltas carry no valence color — counts aren't goals."""
    trend = model.get("trend") or {}
    width = 7 if trend.get("granularity") == "month" else 4
    tiles: list[tuple[str, str, str]] = []
    if model["row_count"] is not None:
        tiles.append(("Rows", _compact(model["row_count"]), ""))
    span = model.get("date_span")
    if span:
        trimmed = "effective_min" in span or "effective_max" in span
        lo = span.get("effective_min") or str(span["min"])[:width]
        hi = span.get("effective_max") or str(span["max"])[:width]
        sub = f"<code>{_esc(span['field'])}</code>"
        if trimmed:
            sub = (
                f"raw {_esc(str(span['min'])[:10])} → "
                f"{_esc(str(span['max'])[:10])} · artifacts trimmed"
            )
        tiles.append(("Date span", f"{_esc(lo)} → {_esc(hi)}", sub))
    if model["data_updated_at"]:
        tiles.append(("Data updated", _esc(str(model["data_updated_at"])[:10]), ""))
    delta = trend.get("delta")
    if delta:
        tiles.append(
            (
                f"Rows per {_esc(trend['granularity'])}",
                f"{delta['pct']:+.1%}",
                f"{_esc(str(delta['to'])[:width])} vs {_esc(str(delta['from'])[:width])}",
            )
        )
    if len(tiles) < 2:
        return ""
    cells = "".join(
        f'<div class="tile"><span class="label">{label}</span>'
        f'<span class="value">{value}</span>'
        + (f'<span class="sub">{sub}</span>' if sub else "")
        + "</div>"
        for label, value, sub in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _notes_html(notes: list[str]) -> str:
    if not notes:
        return ""
    items = "".join(f"<li>{_esc(note)}</li>" for note in notes)
    return (
        '<div class="card note">'
        f'<ul style="margin:0;padding-left:18px">{items}</ul></div>'
    )


def _trend_html(model: dict[str, Any]) -> str:
    trend = model["trend"]
    width = 4 if trend["granularity"] == "year" else 7
    points = [(str(p["bucket"])[:width], p["n"]) for p in trend["points"]]
    partial = bool(trend.get("last_partial"))
    svg = column_chart_svg(
        points,
        aria_label=f"Row count per {trend['granularity']}",
        partial_last=partial,
    )
    note = ""
    if partial:
        note = (
            '<p class="note">The last bucket is the current, incomplete '
            f"{_esc(trend['granularity'])}.</p>"
        )
    return (
        f'<section id="trend"><h2>Rows per {_esc(trend["granularity"])} — '
        f'<code>{_esc(trend["field"])}</code></h2>'
        f'<div class="card">{svg}{note}</div></section>'
    )


def _categories_html(model: dict[str, Any]) -> str:
    blocks = []
    for cat in model["categories"]:
        values = [
            ("(null)" if v["value"] is None else str(v["value"]), v["count"] or 0)
            for v in cat["values"]
        ]
        svg = bar_chart_svg(
            values, aria_label=f"Top values of {cat['field_name']}"
        )
        coverage = ""
        if cat["coverage"] is not None and cat["coverage"] < 1:
            coverage = (
                f'<p class="note">Top {len(values)} values cover '
                f'{cat["coverage"]:.0%} of non-null rows.</p>'
            )
        blocks.append(
            f'<h2><code>{_esc(cat["field_name"])}</code> — '
            f'{cat["distinct_count"]} distinct</h2>'
            f'<div class="card">{svg}{coverage}</div>'
        )
    return f'<section id="categories">{"".join(blocks)}</section>'


def _numeric_html(model: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td><code>{_esc(c['field_name'])}</code></td>"
        f"<td>{_fmt_num(c.get('min'))}</td><td>{_fmt_num(c.get('max'))}</td>"
        f"<td>{_fmt_num(c.get('avg'))}</td><td>{_pct(c.get('null_rate'))}</td></tr>"
        for c in model["numeric"]
    )
    return (
        '<section id="numeric"><h2>Numeric columns</h2><div class="card">'
        "<table><thead><tr><th>Column</th><th>Min</th><th>Max</th><th>Avg</th>"
        f"<th>Null</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
    )


def _quality_html(model: dict[str, Any]) -> str:
    quality = model["quality"]
    parts = ['<section id="quality"><h2>Data quality</h2>']
    if quality["flags"]:
        rows = "".join(
            f"<tr><td><code>{_esc(f['field_name'])}</code></td>"
            f"<td>{_esc(FLAG_LABELS.get(f['flag'], f['flag']))}</td>"
            f"<td>{_esc(f['detail'])}</td>"
            f"<td>{_esc(FLAG_IMPACTS.get(f['flag'], ''))}</td></tr>"
            for f in quality["flags"]
        )
        parts.append(
            '<div class="card"><table><thead><tr><th>Column</th><th>Flag</th>'
            f"<th>Detail</th><th>Impact</th></tr></thead><tbody>{rows}</tbody>"
            "</table></div>"
        )
    rows = "".join(
        f"<tr><td><code>{_esc(c['field_name'])}</code></td>"
        f"<td>{_esc(c.get('type') or '')}</td><td>{_pct(c.get('null_rate'))}</td>"
        f"<td>{c['distinct_count'] if c.get('distinct_count') is not None else '—'}"
        "</td></tr>"
        for c in quality["null_rates"]
    )
    parts.append(
        '<div class="card"><table><thead><tr><th>Column</th><th>Type</th>'
        f"<th>Null</th><th>Distinct</th></tr></thead><tbody>{rows}</tbody>"
        "</table></div>"
    )
    if quality["profile_notes"]:
        items = "".join(f"<li>{_esc(n)}</li>" for n in quality["profile_notes"])
        parts.append(f'<ul class="note">{items}</ul>')
    parts.append("</section>")
    return "".join(parts)


def _footer_html(model: dict[str, Any]) -> str:
    queries = "".join(f"<li><code>{_esc(q)}</code></li>" for q in model["queries"])
    query_block = f"<p>Queries run:</p><ul>{queries}</ul>" if queries else ""
    return (
        f"<footer>{query_block}"
        f"<p>Generated {_esc(model['generated_at'])} by socrata-mcp "
        f"{_esc(model['version'])}. Reflects the dataset as of generation "
        "time; aggregates computed portal-side.</p></footer>"
    )


_SECTION_RENDERERS = {
    "trend": _trend_html,
    "categories": _categories_html,
    "numeric": _numeric_html,
    "quality": _quality_html,
}


def render_html(model: dict[str, Any]) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(model['title'])}</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        _header_html(model),
        _tiles_html(model),
        _notes_html(model["notes"]),
    ]
    for section in model["sections"]:
        parts.append(_SECTION_RENDERERS[section](model))
    parts.append(_footer_html(model))
    parts.append("</main></body></html>")
    return "\n".join(part for part in parts if part)
