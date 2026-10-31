from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from wslmetrics.storage import MetricsStore


def _parse_bound(value: str | None, *, default: int) -> int:
    if value is None or value == "":
        return default
    value = value.strip()
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    # ISO-8601
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _window(
    from_ts: str | None, to_ts: str | None
) -> tuple[int, int]:
    now = int(time.time())
    start = _parse_bound(from_ts, default=now - 24 * 3600)
    end = _parse_bound(to_ts, default=now)
    if start > end:
        start, end = end, start
    return start, end


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_SAMPLE_COLS = [
    "ts",
    "iso",
    "cpu",
    "mem_percent",
    "mem_used",
    "mem_total",
    "docker_mem",
    "cpu_queue_1m",
    "disk_root_percent",
    "disk_mntc_percent",
    "disk_read_bytes",
    "disk_write_bytes",
    "net_rx_bytes",
    "net_tx_bytes",
]


def _sample_row(p) -> list:
    return [
        p.ts,
        _iso(p.ts),
        f"{p.cpu:.2f}",
        f"{p.mem_percent:.2f}",
        p.mem_used,
        p.mem_total,
        p.docker_mem,
        f"{p.cpu_queue_1m:.4f}",
        "" if p.disk_root_percent is None else f"{p.disk_root_percent:.2f}",
        "" if p.disk_mntc_percent is None else f"{p.disk_mntc_percent:.2f}",
        p.disk_read_bytes,
        p.disk_write_bytes,
        p.net_rx_bytes,
        p.net_tx_bytes,
    ]


def _sample_dict(p) -> dict:
    return {
        "ts": p.ts,
        "iso": _iso(p.ts),
        "cpu": round(p.cpu, 4),
        "mem_percent": round(p.mem_percent, 4),
        "mem_used": p.mem_used,
        "mem_total": p.mem_total,
        "docker_mem": p.docker_mem,
        "cpu_queue_1m": round(p.cpu_queue_1m, 4),
        "disk_root_percent": (
            None if p.disk_root_percent is None else round(p.disk_root_percent, 4)
        ),
        "disk_mntc_percent": (
            None if p.disk_mntc_percent is None else round(p.disk_mntc_percent, 4)
        ),
        "disk_read_bytes": p.disk_read_bytes,
        "disk_write_bytes": p.disk_write_bytes,
        "net_rx_bytes": p.net_rx_bytes,
        "net_tx_bytes": p.net_tx_bytes,
    }


def export_csv(
    output: Path,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    include_processes: bool = False,
    include_containers: bool = False,
    include_groups: bool = False,
) -> Path:
    start, end = _window(from_ts, to_ts)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with MetricsStore() as store:
        points = store.query_samples(start=start, end=end, prefer="auto")
        with output.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(_SAMPLE_COLS)
            for p in points:
                writer.writerow(_sample_row(p))

        if include_processes:
            proc_path = output.with_name(output.stem + "_processes" + output.suffix)
            rows = store.query_processes(start=start, end=end)
            with proc_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["ts", "iso", "rank", "name", "pid", "cpu", "rss"])
                for r in rows:
                    writer.writerow(
                        [
                            r["ts"],
                            _iso(int(r["ts"])),
                            r["rank"],
                            r["name"],
                            r["pid"],
                            f"{float(r['cpu']):.2f}",
                            r["rss"],
                        ]
                    )

        if include_containers:
            c_path = output.with_name(output.stem + "_containers" + output.suffix)
            rows = store.query_containers(start=start, end=end)
            with c_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["ts", "iso", "name", "cpu", "mem", "status"])
                for r in rows:
                    writer.writerow(
                        [
                            r["ts"],
                            _iso(int(r["ts"])),
                            r["name"],
                            f"{float(r['cpu']):.2f}",
                            r["mem"],
                            r["status"],
                        ]
                    )

        if include_groups:
            g_path = output.with_name(output.stem + "_groups" + output.suffix)
            rows = store.query_process_groups(start=start, end=end)
            with g_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["ts", "iso", "name", "count", "cpu", "rss"])
                for r in rows:
                    writer.writerow(
                        [
                            r["ts"],
                            _iso(int(r["ts"])),
                            r["name"],
                            r["count"],
                            f"{float(r['cpu']):.2f}",
                            r["rss"],
                        ]
                    )

    return output


def export_json(
    output: Path,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    include_processes: bool = False,
    include_containers: bool = False,
    include_groups: bool = False,
) -> Path:
    start, end = _window(from_ts, to_ts)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload: dict = {
        "from": start,
        "to": end,
        "from_iso": _iso(start),
        "to_iso": _iso(end),
        "samples": [],
    }

    with MetricsStore() as store:
        points = store.query_samples(start=start, end=end, prefer="auto")
        payload["samples"] = [_sample_dict(p) for p in points]

        if include_processes:
            payload["processes"] = [
                {
                    "ts": int(r["ts"]),
                    "iso": _iso(int(r["ts"])),
                    "rank": int(r["rank"]),
                    "name": str(r["name"]),
                    "pid": int(r["pid"]),
                    "cpu": float(r["cpu"]),
                    "rss": int(r["rss"]),
                }
                for r in store.query_processes(start=start, end=end)
            ]

        if include_containers:
            payload["containers"] = [
                {
                    "ts": int(r["ts"]),
                    "iso": _iso(int(r["ts"])),
                    "name": str(r["name"]),
                    "cpu": float(r["cpu"]),
                    "mem": int(r["mem"]),
                    "status": str(r["status"]),
                }
                for r in store.query_containers(start=start, end=end)
            ]

        if include_groups:
            payload["groups"] = [
                {
                    "ts": int(r["ts"]),
                    "iso": _iso(int(r["ts"])),
                    "name": str(r["name"]),
                    "count": int(r["count"]),
                    "cpu": float(r["cpu"]),
                    "rss": int(r["rss"]),
                }
                for r in store.query_process_groups(start=start, end=end)
            ]

    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output
