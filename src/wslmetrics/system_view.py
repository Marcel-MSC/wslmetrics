from __future__ import annotations

from wslmetrics.formatting import format_bytes, format_percent, format_rate
from wslmetrics.models import SystemSummary


def _bar(percent: float, width: int = 20) -> str:
    pct = max(0.0, min(100.0, percent))
    filled = int(round(pct / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def render_system_text(
    summary: SystemSummary,
    *,
    rates: dict[str, float | None] | None = None,
    bar_width: int = 24,
) -> str:
    """Build System tab: load, per-core CPU, mounts, disk/net I/O rates."""
    rates = rates or {}
    lines: list[str] = []

    lines.append(
        f"CPU queue  {summary.cpu_queue_1m:.2f}  {summary.cpu_queue_5m:.2f}  "
        f"{summary.cpu_queue_15m:.2f}  (1m / 5m / 15m)"
    )
    lines.append("")

    cores = summary.cpu_per_core
    lines.append(f"CPU per core  ({len(cores)} cores)  ·  total {format_percent(summary.cpu_percent)}")
    if not cores:
        lines.append("  (no per-core data)")
    else:
        for i, pct in enumerate(cores):
            lines.append(
                f"  cpu{i:<3} {_bar(pct, bar_width)} {format_percent(pct)}"
            )
    lines.append("")

    lines.append("Disk mounts")
    if not summary.disk_mounts:
        lines.append("  (no mounts)")
    else:
        lines.append(
            f"  {'PATH':<12} {'USED':>10} {'TOTAL':>10} {'FREE':>10} {'%':>7}"
        )
        lines.append("  " + "-" * 52)
        for m in summary.disk_mounts:
            lines.append(
                f"  {m.path:<12} {format_bytes(m.used):>10} "
                f"{format_bytes(m.total):>10} {format_bytes(m.free):>10} "
                f"{format_percent(m.percent):>7}"
            )
    lines.append("")

    disk_r = rates.get("disk_read")
    disk_w = rates.get("disk_write")
    lines.append("Disk I/O")
    if summary.disk_io is None:
        lines.append("  (unavailable)")
    else:
        lines.append(
            f"  read  {format_rate(disk_r)}  ·  write {format_rate(disk_w)}"
        )
        lines.append(
            f"  counters  r={format_bytes(summary.disk_io.read_bytes)}  "
            f"w={format_bytes(summary.disk_io.write_bytes)}"
        )
    lines.append("")

    net_rx = rates.get("net_rx")
    net_tx = rates.get("net_tx")
    lines.append("Network")
    if summary.net_io is None:
        lines.append("  (unavailable)")
    else:
        n = summary.net_io
        lines.append(
            f"  {n.iface}  ↓ {format_rate(net_rx)}  ·  ↑ {format_rate(net_tx)}"
        )
        lines.append(
            f"  counters  rx={format_bytes(n.rx_bytes)}  tx={format_bytes(n.tx_bytes)}"
        )

    return "\n".join(lines).rstrip() + "\n"


def summary_line2(
    summary: SystemSummary,
    *,
    rates: dict[str, float | None] | None = None,
) -> str:
    """Compact second summary line: CPU queue · disks · net rates."""
    rates = rates or {}
    parts: list[str] = [f"CPU queue {summary.cpu_queue_1m:.2f}"]
    for m in summary.disk_mounts:
        parts.append(f"Disk {m.path} {format_percent(m.percent)}")
    if summary.net_io is not None:
        parts.append(
            f"Net ↓{format_rate(rates.get('net_rx'))} ↑{format_rate(rates.get('net_tx'))}"
        )
    return "  ·  ".join(parts)
