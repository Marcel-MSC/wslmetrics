from __future__ import annotations

from typing import Any

from wslmetrics.models import ContainerRow


def _cpu_percent(stats: dict[str, Any]) -> float:
    """Docker's official CPU% formula from container stats."""
    cpu_stats = stats.get("cpu_stats") or {}
    precpu_stats = stats.get("precpu_stats") or {}

    cpu_usage = (cpu_stats.get("cpu_usage") or {}).get("total_usage", 0) or 0
    precpu_usage = (precpu_stats.get("cpu_usage") or {}).get("total_usage", 0) or 0
    system_usage = cpu_stats.get("system_cpu_usage", 0) or 0
    presystem_usage = precpu_stats.get("system_cpu_usage", 0) or 0

    cpu_delta = float(cpu_usage) - float(precpu_usage)
    system_delta = float(system_usage) - float(presystem_usage)
    if cpu_delta <= 0.0 or system_delta <= 0.0:
        return 0.0

    online = cpu_stats.get("online_cpus")
    if not online:
        percpu = (cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or []
        online = len(percpu) or 1

    return (cpu_delta / system_delta) * float(online) * 100.0


def _mem_usage(stats: dict[str, Any]) -> tuple[int, int, float]:
    memory = stats.get("memory_stats") or {}
    usage = int(memory.get("usage") or 0)
    limit = int(memory.get("limit") or 0)
    stats_detail = memory.get("stats") or {}

    # Prefer usage minus reclaimable cache when available.
    cache = int(
        stats_detail.get("total_inactive_file")
        or stats_detail.get("inactive_file")
        or stats_detail.get("cache")
        or 0
    )
    working_set = max(0, usage - cache)
    if limit > 0:
        percent = (working_set / limit) * 100.0
    else:
        percent = 0.0
    return working_set, limit, percent


def _container_image(container: Any) -> str:
    """Read image from list attrs — avoid container.image API round-trip."""
    attrs = getattr(container, "attrs", None) or {}
    config = attrs.get("Config") or {}
    image = config.get("Image") or attrs.get("Image")
    if image:
        return str(image)
    return "?"


class DockerCollector:
    """Collect per-container CPU/RAM via the Docker Engine API."""

    def __init__(self) -> None:
        self._client: Any | None = None
        self._error: str | None = None
        self._connect()

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def error(self) -> str | None:
        return self._error

    def _connect(self) -> None:
        try:
            import docker
            from docker.errors import DockerException
        except ImportError as exc:
            self._client = None
            self._error = f"docker SDK missing: {exc}"
            return

        try:
            client = docker.from_env()
            client.ping()
            self._client = client
            self._error = None
        except DockerException as exc:
            self._client = None
            self._error = str(exc)
        except Exception as exc:  # noqa: BLE001 — surface any daemon/socket failure
            self._client = None
            self._error = str(exc)

    def collect(self, *, all_containers: bool = True) -> tuple[tuple[ContainerRow, ...], int]:
        """Return container rows and total memory used by containers."""
        if self._client is None:
            self._connect()
        if self._client is None:
            return (), 0

        rows: list[ContainerRow] = []
        total_mem = 0
        try:
            containers = self._client.containers.list(all=all_containers)
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            self._client = None
            return (), 0

        for container in containers:
            try:
                name = (container.name or container.short_id or "?").lstrip("/")
                image = _container_image(container)
                status = str(container.status or "?")

                if status != "running":
                    rows.append(
                        ContainerRow(
                            name=name,
                            short_id=container.short_id,
                            cpu_percent=0.0,
                            mem_usage_bytes=0,
                            mem_limit_bytes=0,
                            mem_percent=0.0,
                            status=status,
                            image=image,
                        )
                    )
                    continue

                stats = container.stats(stream=False)
                cpu = _cpu_percent(stats)
                mem_usage, mem_limit, mem_percent = _mem_usage(stats)
                total_mem += mem_usage
                rows.append(
                    ContainerRow(
                        name=name,
                        short_id=container.short_id,
                        cpu_percent=cpu,
                        mem_usage_bytes=mem_usage,
                        mem_limit_bytes=mem_limit,
                        mem_percent=mem_percent,
                        status=status,
                        image=image,
                    )
                )
            except Exception:  # noqa: BLE001 — skip unhealthy containers
                continue

        rows.sort(key=lambda r: (r.cpu_percent, r.mem_usage_bytes), reverse=True)
        self._error = None
        return tuple(rows), total_mem
