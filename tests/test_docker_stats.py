from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wslmetrics.collectors.docker_stats import (
    DockerCollector,
    _container_image,
    _cpu_percent,
    _mem_usage,
)


def test_cpu_percent_formula() -> None:
    stats = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200, "percpu_usage": [1, 1]},
            "system_cpu_usage": 1000,
            "online_cpus": 2,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100},
            "system_cpu_usage": 500,
        },
    }
    # (100/500)*2*100 = 40
    assert abs(_cpu_percent(stats) - 40.0) < 0.01


def test_cpu_percent_zero_delta() -> None:
    assert _cpu_percent({"cpu_stats": {}, "precpu_stats": {}}) == 0.0


def test_mem_usage_subtracts_cache() -> None:
    stats = {
        "memory_stats": {
            "usage": 1000,
            "limit": 2000,
            "stats": {"inactive_file": 200},
        }
    }
    usage, limit, pct = _mem_usage(stats)
    assert usage == 800
    assert limit == 2000
    assert pct == 40.0


def test_container_image_from_attrs() -> None:
    container = SimpleNamespace(
        attrs={"Config": {"Image": "postgres:16"}, "Image": "sha256:abc"}
    )
    assert _container_image(container) == "postgres:16"


def test_docker_collector_unavailable() -> None:
    collector = DockerCollector.__new__(DockerCollector)
    collector._client = None
    collector._error = "offline"

    def _fail_connect() -> None:
        collector._client = None
        collector._error = "offline"

    collector._connect = _fail_connect  # type: ignore[method-assign]
    rows, total = collector.collect()
    assert rows == ()
    assert total == 0
    assert collector.available is False


def test_docker_collector_lists_running() -> None:
    running = MagicMock()
    running.name = "api"
    running.short_id = "abc"
    running.status = "running"
    running.attrs = {"Config": {"Image": "api:latest"}}
    running.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 200},
            "system_cpu_usage": 1000,
            "online_cpus": 1,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 100},
            "system_cpu_usage": 500,
        },
        "memory_stats": {"usage": 100, "limit": 200, "stats": {}},
    }

    stopped = MagicMock()
    stopped.name = "old"
    stopped.short_id = "def"
    stopped.status = "exited"
    stopped.attrs = {"Config": {"Image": "old:latest"}}

    client = MagicMock()
    client.containers.list.return_value = [running, stopped]

    collector = DockerCollector.__new__(DockerCollector)
    collector._client = client
    collector._error = None
    rows, total = collector.collect(all_containers=True)
    assert len(rows) == 2
    by_name = {r.name: r for r in rows}
    assert by_name["old"].cpu_percent == 0.0
    assert by_name["api"].mem_usage_bytes == 100
    assert total == 100
