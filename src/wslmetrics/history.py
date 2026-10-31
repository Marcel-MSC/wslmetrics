from __future__ import annotations

import re
import time
from statistics import mean

import plotext as plt

from wslmetrics.formatting import format_bytes
from wslmetrics.storage import MetricsStore, SamplePoint

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _stats_percent(values: list[float]) -> str:
    if not values:
        return "no data"
    return f"min/avg/max {min(values):.0f}/{mean(values):.0f}/{max(values):.0f}%"


def _stats_cpu_queue(values: list[float]) -> str:
    if not values:
        return "no data"
    return f"min/avg/max {min(values):.2f}/{mean(values):.2f}/{max(values):.2f}"


def _stats_bytes(values: list[float]) -> str:
    if not values:
        return "no data"
    return (
        f"min/avg/max {format_bytes(int(min(values)))}/"
        f"{format_bytes(int(mean(values)))}/"
        f"{format_bytes(int(max(values)))}"
    )


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
    ylim: tuple[float, float] | None = (0, 100),
) -> str:
    plt.clear_figure()
    plt.plotsize(width, height)
    try:
        plt.theme("clear")
    except Exception:  # noqa: BLE001
        pass
    plt.plot(values)
    plt.title(title)
    if ylim is not None:
        plt.ylim(*ylim)
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
        stats = _stats_percent(values)
        ylim: tuple[float, float] | None = (0, 100)
    elif metric == "cpu":
        values = [p.cpu for p in points]
        label = "CPU %"
        stats = _stats_percent(values)
        ylim = (0, 100)
    elif metric == "docker_mem":
        values = [float(p.docker_mem) for p in points]
        label = "Docker mem"
        if not points or all(v <= 0 for v in values):
            header = f"{title} · {label} · n={len(points)}"
            return [header, "(no docker_mem data)", ""]
        stats = _stats_bytes(values)
        ylim = None
    elif metric == "cpu_queue_1m":
        values = [float(p.cpu_queue_1m) for p in points]
        label = "CPU queue (1m)"
        if not points or all(v <= 0 for v in values):
            header = f"{title} · {label} · n={len(points)}"
            return [header, "(no CPU queue data)", ""]
        stats = _stats_cpu_queue(values)
        ylim = None
    elif metric == "disk_root":
        values = [
            float(p.disk_root_percent)
            for p in points
            if p.disk_root_percent is not None
        ]
        label = "Disk / %"
        if not values:
            header = f"{title} · {label} · n={len(points)}"
            return [header, "(no disk / data)", ""]
        stats = _stats_percent(values)
        ylim = (0, 100)
    elif metric == "disk_mntc":
        values = [
            float(p.disk_mntc_percent)
            for p in points
            if p.disk_mntc_percent is not None
        ]
        label = "Disk /mnt/c %"
        if not values:
            header = f"{title} · {label} · n={len(points)}"
            return [header, "(no disk /mnt/c data)", ""]
        stats = _stats_percent(values)
        ylim = (0, 100)
    else:
        raise ValueError(f"unknown metric: {metric}")

    header = f"{title} · {label} · {stats} · n={len(points)}"
    if not points:
        return [header, "(no data)", ""]

    try:
        chart = _plot_series(
            values,
            title=f"{title} {label}",
            width=width,
            height=height,
            ylim=ylim,
        )
        return [header, chart, ""]
    except Exception:  # noqa: BLE001
        spark = _sparkline(values, width=min(48, width - 6))
        return [header, f"{label}  {spark}", ""]


def render_history_text(*, width: int = 72, height: int = 12) -> str:
    """Build History view with RAM %, CPU %, and Docker mem charts."""
    now = int(time.time())
    width = max(40, width)
    chart_h = max(5, min(height, 8))

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
            "CPU %\n(no history yet — run `wslmetrics sample` or install-job)\n\n"
            "Docker mem\n(no history yet — run `wslmetrics sample` or install-job)\n\n"
            "CPU queue (1m)\n(no history yet — run `wslmetrics sample` or install-job)\n\n"
            "Disk / %\n(no history yet — run `wslmetrics sample` or install-job)"
        )

    metrics = ["mem", "cpu", "docker_mem", "cpu_queue_1m", "disk_root"]
    if any(p.disk_mntc_percent is not None for p in series):
        metrics.append("disk_mntc")

    lines: list[str] = []
    for metric in metrics:
        lines.extend(
            _section(
                series,
                title=window,
                metric=metric,
                width=width,
                height=chart_h,
            )
        )
    return "\n".join(lines).rstrip() + "\n"
