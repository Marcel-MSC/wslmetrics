from __future__ import annotations

import time
from dataclasses import replace

import psutil

from wslmetrics.alerts import dispatch_alerts, evaluate_alerts
from wslmetrics.collectors import DockerCollector, HostCollector
from wslmetrics.config import load_config
from wslmetrics.models import SortKey
from wslmetrics.storage import MetricsStore
from wslmetrics.wsl_limit import get_wsl_memory_limit, wsl_usage_percent


def enrich_summary_wsl(summary):
    limit = get_wsl_memory_limit()
    return replace(
        summary,
        wsl_mem_limit=limit,
        wsl_mem_percent=wsl_usage_percent(summary.mem_used, limit),
    )


def run_sample(*, cpu_prime_seconds: float = 0.25) -> dict:
    """Collect one sample, persist, downsample, and evaluate alerts."""
    cfg = load_config()
    # Skip full process prime in __init__; we block once below, then sample.
    host = HostCollector(prime_processes=False)
    docker = DockerCollector()
    # Blocking prime so oneshot systemd runs get a meaningful CPU%.
    psutil.cpu_percent(interval=cpu_prime_seconds)
    host._prime_processes()
    time.sleep(0.05)

    summary = enrich_summary_wsl(host.summary())
    processes = host.processes(top=5, sort_key=SortKey.CPU)
    groups = host.grouped_processes(
        top=15, sort_key=SortKey.MEM, mem_total=summary.mem_total
    )
    # Sample only needs running containers (stored); skip exited image lookups.
    containers, docker_mem = docker.collect(all_containers=False)
    summary = replace(
        summary,
        docker_mem_total=docker_mem,
        docker_available=docker.available,
        docker_error=docker.error,
    )

    with MetricsStore() as store:
        ts = store.insert_sample(
            summary, processes, containers, groups=groups, top_n=5, group_n=15
        )
        store.maintain(now=ts)

    events = evaluate_alerts(summary, cfg.alerts)
    fired = dispatch_alerts(events, cfg.alerts)

    return {
        "ts": ts,
        "cpu": summary.cpu_percent,
        "mem_percent": summary.mem_percent,
        "docker_mem": summary.docker_mem_total,
        "wsl_mem_percent": summary.wsl_mem_percent,
        "alerts_fired": [e.kind for e in fired],
        "docker_available": summary.docker_available,
    }


def format_sample_result(result: dict) -> str:
    alerts = ",".join(result["alerts_fired"]) or "none"
    wsl = (
        f" wsl={result['wsl_mem_percent']:.1f}%"
        if result.get("wsl_mem_percent") is not None
        else ""
    )
    return (
        f"sample ts={result['ts']} cpu={result['cpu']:.1f}% "
        f"mem={result['mem_percent']:.1f}% docker_mem={result['docker_mem']}"
        f"{wsl} alerts={alerts}"
    )
