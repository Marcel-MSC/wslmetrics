from __future__ import annotations

import re
import time
from statistics import mean

import plotext as plt

from wslmetrics.storage import MetricsStore, SamplePoint

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _stats(values: list[float]) -> str:
    if not values:
        return "no data"
    return f"min/avg/max {min(values):.0f}/{mean(values):.0f}/{max(values):.0f}%"


def _sparkline(values: list[float], width: int = 48) -> str:
    if not values:
        return "(no data)"
    blocks = "▁▂▃▄▅▆▇█"
    lo = min(values)
    hi = max(values)
    span = hi - lo or 1.0
    step = max(1, len(values) // width)
    sampled = values[::step][:width]
    chars = []
    for v in sampled:
        idx = int((v - lo) / span * (len(blocks) - 1))
        chars.append(blocks[idx])
    return "".join(chars)


def _plot_series(
    values: list[float],
    *,
    title: str,
    width: int,
    height: int,
) -> str:
    plt.clear_figure()
    plt.plotsize(width, height)
    try:
        plt.theme("clear")
    except Exception:  # noqa: BLE001
        pass
    plt.plot(values)
    plt.title(title)
    plt.ylim(0, 100)
    chart = _ANSI_RE.sub("", plt.build() or "")
    if not chart.strip():
        raise RuntimeError("empty plotext chart")
    return chart


def _section(
    points: list[SamplePoint],
    *,
    title: str,
    metric: str,
    width: int,
    height: int,
) -> list[str]:
    if metric == "mem":
        values = [p.mem_percent for p in points]
        label = "RAM %"
    else:
        values = [p.cpu for p in points]
        label = "CPU %"

    header = f"{title} · {label} · {_stats(values)} · n={len(points)}"
    if not points:
        return [header, "(no data)", ""]

    try:
        chart = _plot_series(
            values,
            title=f"{title} {label}",
            width=width,
            height=height,
        )
        return [header, chart, ""]
    except Exception:  # noqa: BLE001
        spark = _sparkline(values, width=min(48, width - 6))
        return [header, f"{label}  {spark}", ""]


def render_history_text(*, width: int = 72, height: int = 12) -> str:
    """Build History view with separate RAM (top) and CPU (bottom) charts."""
    now = int(time.time())
    width = max(40, width)
    # Split vertical budget: two charts + headers.
    chart_h = max(6, min(height, 10))

    with MetricsStore() as store:
        day = store.query_samples(start=now - 24 * 3600, end=now, prefer="5m")
        if not day:
            day = store.query_samples(start=now - 24 * 3600, end=now, prefer="raw")
        hour = store.query_samples(start=now - 3600, end=now, prefer="raw")

    # Prefer 24h series; fall back to 1h if history is still short.
    series = day if len(day) >= 2 else hour
    window = "24h" if series is day and day else "1h"

    if not series:
        return (
            "RAM %\n(no history yet — run `wslmetrics sample` or install-job)\n\n"
            "CPU %\n(no history yet — run `wslmetrics sample` or install-job)"
        )

    lines: list[str] = []
    lines.extend(
        _section(
            series,
            title=window,
            metric="mem",
            width=width,
            height=chart_h,
        )
    )
    lines.extend(
        _section(
            series,
            title=window,
            metric="cpu",
            width=width,
            height=chart_h,
        )
    )
    return "\n".join(lines).rstrip() + "\n"
