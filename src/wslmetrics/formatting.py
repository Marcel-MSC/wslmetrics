from __future__ import annotations


def format_bytes(num: int | float) -> str:
    """Format byte counts as human-readable binary units."""
    value = float(num)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TiB"


def format_percent(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}%"


def format_rate(bytes_per_sec: float | None) -> str:
    """Format a byte/s rate; em-dash when unknown."""
    if bytes_per_sec is None:
        return "—"
    return f"{format_bytes(bytes_per_sec)}/s"
