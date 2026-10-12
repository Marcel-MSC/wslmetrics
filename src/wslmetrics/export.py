from __future__ import annotations

import csv
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


def export_csv(
    output: Path,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    include_processes: bool = False,
) -> Path:
    now = int(time.time())
    start = _parse_bound(from_ts, default=now - 24 * 3600)
    end = _parse_bound(to_ts, default=now)
    if start > end:
        start, end = end, start

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with MetricsStore() as store:
        points = store.query_samples(start=start, end=end, prefer="auto")
        with output.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "ts",
                    "iso",
                    "cpu",
                    "mem_percent",
                    "mem_used",
                    "mem_total",
                    "docker_mem",
                ]
            )
            for p in points:
                iso = datetime.fromtimestamp(p.ts, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                writer.writerow(
                    [
                        p.ts,
                        iso,
                        f"{p.cpu:.2f}",
                        f"{p.mem_percent:.2f}",
                        p.mem_used,
                        p.mem_total,
                        p.docker_mem,
                    ]
                )

        if include_processes:
            proc_path = output.with_name(output.stem + "_processes" + output.suffix)
            rows = store.query_processes(start=start, end=end)
            with proc_path.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["ts", "iso", "rank", "name", "pid", "cpu", "rss"])
                for r in rows:
                    iso = datetime.fromtimestamp(int(r["ts"]), tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    writer.writerow(
                        [
                            r["ts"],
                            iso,
                            r["rank"],
                            r["name"],
                            r["pid"],
                            f"{float(r['cpu']):.2f}",
                            r["rss"],
                        ]
                    )

    return output
