from __future__ import annotations

from wslmetrics.models import DiskIO, NetIO, SystemSummary


def counter_rate(prev: int | None, curr: int, dt: float) -> float | None:
    """Bytes/s from cumulative counters. None if not yet computable."""
    if prev is None or dt <= 0:
        return None
    delta = curr - prev
    if delta < 0:
        # Counter reset (reboot / wrap).
        return None
    return delta / dt


def disk_io_rates(
    prev: DiskIO | None, curr: DiskIO | None, dt: float
) -> tuple[float | None, float | None]:
    if prev is None or curr is None:
        return None, None
    return (
        counter_rate(prev.read_bytes, curr.read_bytes, dt),
        counter_rate(prev.write_bytes, curr.write_bytes, dt),
    )


def net_io_rates(
    prev: NetIO | None, curr: NetIO | None, dt: float
) -> tuple[float | None, float | None]:
    if prev is None or curr is None:
        return None, None
    if prev.iface != curr.iface:
        return None, None
    return (
        counter_rate(prev.rx_bytes, curr.rx_bytes, dt),
        counter_rate(prev.tx_bytes, curr.tx_bytes, dt),
    )


def summary_io_rates(
    prev: SystemSummary | None,
    curr: SystemSummary,
    dt: float,
) -> dict[str, float | None]:
    """Return disk_read/write and net_rx/tx rates in bytes/s (or None)."""
    disk_r, disk_w = disk_io_rates(
        prev.disk_io if prev else None, curr.disk_io, dt
    )
    net_rx, net_tx = net_io_rates(
        prev.net_io if prev else None, curr.net_io, dt
    )
    return {
        "disk_read": disk_r,
        "disk_write": disk_w,
        "net_rx": net_rx,
        "net_tx": net_tx,
    }
