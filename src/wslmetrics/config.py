from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from wslmetrics.paths import CONFIG_PATH, ensure_dirs

DEFAULT_CONFIG: dict[str, Any] = {
    "alerts": {
        "mem_percent": 90,
        "cpu_percent": 95,
        "docker_mem_bytes": None,
        "wsl_mem_percent": 90,
        "swap_percent": None,
        "cooldown_seconds": 300,
    },
    "sample_interval_seconds": 30,
}


@dataclass(frozen=True, slots=True)
class AlertConfig:
    mem_percent: float
    cpu_percent: float
    docker_mem_bytes: int | None
    wsl_mem_percent: float | None
    swap_percent: float | None
    cooldown_seconds: int


@dataclass(frozen=True, slots=True)
class AppConfig:
    alerts: AlertConfig
    sample_interval_seconds: int


def default_config_text() -> str:
    return yaml.safe_dump(DEFAULT_CONFIG, default_flow_style=False, sort_keys=False)


def ensure_default_config(path: Path | None = None) -> Path:
    ensure_dirs()
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(default_config_text(), encoding="utf-8")
    return cfg_path


def load_config(path: Path | None = None) -> AppConfig:
    cfg_path = ensure_default_config(path)
    raw: dict[str, Any] = {}
    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, yaml.YAMLError):
        raw = {}

    alerts_raw = raw.get("alerts") if isinstance(raw.get("alerts"), dict) else {}
    defaults = DEFAULT_CONFIG["alerts"]

    docker_mem = alerts_raw.get("docker_mem_bytes", defaults["docker_mem_bytes"])
    if docker_mem is not None:
        try:
            docker_mem = int(docker_mem)
        except (TypeError, ValueError):
            docker_mem = None

    wsl_mem = alerts_raw.get("wsl_mem_percent", defaults["wsl_mem_percent"])
    if wsl_mem is not None:
        try:
            wsl_mem = float(wsl_mem)
        except (TypeError, ValueError):
            wsl_mem = None

    swap_pct = alerts_raw.get("swap_percent", defaults["swap_percent"])
    if swap_pct is not None:
        try:
            swap_pct = float(swap_pct)
        except (TypeError, ValueError):
            swap_pct = None

    try:
        interval = int(
            raw.get(
                "sample_interval_seconds",
                DEFAULT_CONFIG["sample_interval_seconds"],
            )
        )
    except (TypeError, ValueError):
        interval = int(DEFAULT_CONFIG["sample_interval_seconds"])

    return AppConfig(
        alerts=AlertConfig(
            mem_percent=float(
                alerts_raw.get("mem_percent", defaults["mem_percent"])
            ),
            cpu_percent=float(
                alerts_raw.get("cpu_percent", defaults["cpu_percent"])
            ),
            docker_mem_bytes=docker_mem,
            wsl_mem_percent=wsl_mem,
            swap_percent=swap_pct,
            cooldown_seconds=int(
                alerts_raw.get("cooldown_seconds", defaults["cooldown_seconds"])
            ),
        ),
        sample_interval_seconds=max(5, interval),
    )
