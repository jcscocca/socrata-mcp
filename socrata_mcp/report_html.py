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


def _rounded_top(x: float, y_top: float, w: float, h: float) -> str:
    """Column with a 4px rounded data-end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4.0, h, w / 2)
    return (
        f'<path class="bar" d="M{x:.1f},{y_top + h:.1f} L{x:.1f},{y_top + r:.1f} '
        f"Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} "
        f"L{x + w - r:.1f},{y_top:.1f} "
        f"Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} "
        f'L{x + w:.1f},{y_top + h:.1f} Z"/>'
    )


def _rounded_right(x: float, y: float, w: float, h: float) -> str:
    """Horizontal bar with a 4px rounded data-end, square at the baseline."""
    if w <= 0:
        return ""
    r = min(4.0, w, h / 2)
    return (
        f'<path class="bar" d="M{x:.1f},{y:.1f} L{x + w - r:.1f},{y:.1f} '
        f"Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} "
        f"L{x + w:.1f},{y + h - r:.1f} "
        f"Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f} "
        f'L{x:.1f},{y + h:.1f} Z"/>'
    )


def column_chart_svg(points: list[tuple[str, float]], *, aria_label: str) -> str:
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
        parts.append(_rounded_top(cx - bar_w / 2, y(value), bar_w, y(0) - y(value)))
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
        parts.append(_rounded_right(left, yy, w, bar_h))
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
