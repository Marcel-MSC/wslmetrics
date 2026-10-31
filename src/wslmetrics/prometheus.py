from __future__ import annotations

from pathlib import Path

from wslmetrics.models import SystemSummary
from wslmetrics.paths import PROM_TEXTFILE, ensure_dirs


def render_prom_text(summary: SystemSummary) -> str:
    """Prometheus textfile exposition for node_exporter collector."""
    lines: list[str] = [
        "# HELP wslmetrics_cpu_percent Host CPU usage percent.",
        "# TYPE wslmetrics_cpu_percent gauge",
        f"wslmetrics_cpu_percent {summary.cpu_percent:.4f}",
        "# HELP wslmetrics_mem_percent Host memory usage percent.",
        "# TYPE wslmetrics_mem_percent gauge",
        f"wslmetrics_mem_percent {summary.mem_percent:.4f}",
        "# HELP wslmetrics_mem_used_bytes Host memory used bytes.",
        "# TYPE wslmetrics_mem_used_bytes gauge",
        f"wslmetrics_mem_used_bytes {int(summary.mem_used)}",
        "# HELP wslmetrics_mem_total_bytes Host memory total bytes.",
        "# TYPE wslmetrics_mem_total_bytes gauge",
        f"wslmetrics_mem_total_bytes {int(summary.mem_total)}",
        "# HELP wslmetrics_swap_percent Host swap usage percent.",
        "# TYPE wslmetrics_swap_percent gauge",
        f"wslmetrics_swap_percent {summary.swap_percent:.4f}",
        "# HELP wslmetrics_cpu_queue_1m CPU queue (load average) 1 minute.",
        "# TYPE wslmetrics_cpu_queue_1m gauge",
        f"wslmetrics_cpu_queue_1m {summary.cpu_queue_1m:.4f}",
        "# HELP wslmetrics_cpu_queue_5m CPU queue (load average) 5 minutes.",
        "# TYPE wslmetrics_cpu_queue_5m gauge",
        f"wslmetrics_cpu_queue_5m {summary.cpu_queue_5m:.4f}",
        "# HELP wslmetrics_cpu_queue_15m CPU queue (load average) 15 minutes.",
        "# TYPE wslmetrics_cpu_queue_15m gauge",
        f"wslmetrics_cpu_queue_15m {summary.cpu_queue_15m:.4f}",
        "# HELP wslmetrics_docker_mem_bytes Sum of Docker container working-set memory.",
        "# TYPE wslmetrics_docker_mem_bytes gauge",
        f"wslmetrics_docker_mem_bytes {int(summary.docker_mem_total)}",
    ]

    if summary.disk_io is not None:
        lines.extend(
            [
                "# HELP wslmetrics_disk_read_bytes_total Disk read bytes (cumulative).",
                "# TYPE wslmetrics_disk_read_bytes_total counter",
                f"wslmetrics_disk_read_bytes_total {int(summary.disk_io.read_bytes)}",
                "# HELP wslmetrics_disk_write_bytes_total Disk write bytes (cumulative).",
                "# TYPE wslmetrics_disk_write_bytes_total counter",
                f"wslmetrics_disk_write_bytes_total {int(summary.disk_io.write_bytes)}",
            ]
        )

    if summary.net_io is not None:
        iface = summary.net_io.iface.replace('"', "")
        lines.extend(
            [
                "# HELP wslmetrics_net_rx_bytes_total Network receive bytes (cumulative).",
                "# TYPE wslmetrics_net_rx_bytes_total counter",
                f'wslmetrics_net_rx_bytes_total{{iface="{iface}"}} '
                f"{int(summary.net_io.rx_bytes)}",
                "# HELP wslmetrics_net_tx_bytes_total Network transmit bytes (cumulative).",
                "# TYPE wslmetrics_net_tx_bytes_total counter",
                f'wslmetrics_net_tx_bytes_total{{iface="{iface}"}} '
                f"{int(summary.net_io.tx_bytes)}",
            ]
        )

    if summary.disk_mounts:
        lines.extend(
            [
                "# HELP wslmetrics_disk_used_percent Filesystem used percent.",
                "# TYPE wslmetrics_disk_used_percent gauge",
            ]
        )
        for m in summary.disk_mounts:
            path = m.path.replace('"', '\\"')
            lines.append(
                f'wslmetrics_disk_used_percent{{path="{path}"}} {m.percent:.4f}'
            )
        lines.extend(
            [
                "# HELP wslmetrics_disk_total_bytes Filesystem total bytes.",
                "# TYPE wslmetrics_disk_total_bytes gauge",
            ]
        )
        for m in summary.disk_mounts:
            path = m.path.replace('"', '\\"')
            lines.append(
                f'wslmetrics_disk_total_bytes{{path="{path}"}} {int(m.total)}'
            )
        lines.extend(
            [
                "# HELP wslmetrics_disk_used_bytes Filesystem used bytes.",
                "# TYPE wslmetrics_disk_used_bytes gauge",
            ]
        )
        for m in summary.disk_mounts:
            path = m.path.replace('"', '\\"')
            lines.append(
                f'wslmetrics_disk_used_bytes{{path="{path}"}} {int(m.used)}'
            )

    if summary.wsl_mem_percent is not None:
        lines.extend(
            [
                "# HELP wslmetrics_wsl_mem_percent Memory vs .wslconfig memory= limit.",
                "# TYPE wslmetrics_wsl_mem_percent gauge",
                f"wslmetrics_wsl_mem_percent {summary.wsl_mem_percent:.4f}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def write_prom_textfile(
    summary: SystemSummary, path: Path | None = None
) -> Path:
    """Atomically write Prometheus textfile under the state dir."""
    ensure_dirs()
    target = path or PROM_TEXTFILE
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_prom_text(summary)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    return target
