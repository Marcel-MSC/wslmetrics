from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from wslmetrics.config import AlertConfig
from wslmetrics.formatting import format_bytes, format_percent
from wslmetrics.models import SystemSummary
from wslmetrics.paths import ALERT_STATE_PATH, ALERTS_LOG, ensure_dirs


@dataclass(frozen=True, slots=True)
class AlertEvent:
    kind: str
    message: str
    value: float
    threshold: float


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    if path == ALERT_STATE_PATH:
        ensure_dirs()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def evaluate_alerts(summary: SystemSummary, cfg: AlertConfig) -> list[AlertEvent]:
    events: list[AlertEvent] = []
    if summary.mem_percent >= cfg.mem_percent:
        events.append(
            AlertEvent(
                kind="mem_percent",
                message=(
                    f"RAM {format_percent(summary.mem_percent)} "
                    f"(threshold {format_percent(cfg.mem_percent)}) — "
                    f"{format_bytes(summary.mem_used)}/{format_bytes(summary.mem_total)}"
                ),
                value=summary.mem_percent,
                threshold=cfg.mem_percent,
            )
        )
    if summary.cpu_percent >= cfg.cpu_percent:
        events.append(
            AlertEvent(
                kind="cpu_percent",
                message=(
                    f"CPU {format_percent(summary.cpu_percent)} "
                    f"(threshold {format_percent(cfg.cpu_percent)})"
                ),
                value=summary.cpu_percent,
                threshold=cfg.cpu_percent,
            )
        )
    if (
        cfg.docker_mem_bytes is not None
        and summary.docker_mem_total >= cfg.docker_mem_bytes
    ):
        events.append(
            AlertEvent(
                kind="docker_mem_bytes",
                message=(
                    f"Docker mem {format_bytes(summary.docker_mem_total)} "
                    f"(threshold {format_bytes(cfg.docker_mem_bytes)})"
                ),
                value=float(summary.docker_mem_total),
                threshold=float(cfg.docker_mem_bytes),
            )
        )
    if (
        cfg.wsl_mem_percent is not None
        and summary.wsl_mem_limit is not None
        and summary.wsl_mem_percent is not None
        and summary.wsl_mem_percent >= cfg.wsl_mem_percent
    ):
        events.append(
            AlertEvent(
                kind="wsl_mem_percent",
                message=(
                    f"WSL RAM {format_percent(summary.wsl_mem_percent)} of "
                    f".wslconfig limit "
                    f"({format_bytes(summary.mem_used)}/"
                    f"{format_bytes(summary.wsl_mem_limit)}; "
                    f"threshold {format_percent(cfg.wsl_mem_percent)})"
                ),
                value=summary.wsl_mem_percent,
                threshold=cfg.wsl_mem_percent,
            )
        )
    if (
        cfg.swap_percent is not None
        and summary.swap_percent >= cfg.swap_percent
    ):
        events.append(
            AlertEvent(
                kind="swap_percent",
                message=(
                    f"Swap {format_percent(summary.swap_percent)} "
                    f"(threshold {format_percent(cfg.swap_percent)}) — "
                    f"{format_bytes(summary.swap_used)}/"
                    f"{format_bytes(summary.swap_total)}"
                ),
                value=summary.swap_percent,
                threshold=cfg.swap_percent,
            )
        )
    return events


def _notify_send(message: str) -> bool:
    binary = shutil.which("notify-send")
    if not binary:
        return False
    try:
        subprocess.run(
            [binary, "--app-name=wslmetrics", "wslmetrics", message],
            check=False,
            timeout=5,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _append_log(message: str, *, log_path: Path = ALERTS_LOG) -> None:
    if log_path == ALERTS_LOG:
        ensure_dirs()
    else:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{ts} {message}\n")


def dispatch_alerts(
    events: list[AlertEvent],
    cfg: AlertConfig,
    *,
    state_path: Path = ALERT_STATE_PATH,
    log_path: Path = ALERTS_LOG,
    now: float | None = None,
) -> list[AlertEvent]:
    """Fire alerts respecting per-kind cooldown. Returns events that were sent."""
    if not events:
        return []

    now_ts = float(now if now is not None else time.time())
    state = _load_state(state_path)
    last_map = state.get("last_alert") if isinstance(state.get("last_alert"), dict) else {}
    fired: list[AlertEvent] = []

    for event in events:
        last = float(last_map.get(event.kind, 0) or 0)
        if now_ts - last < cfg.cooldown_seconds:
            continue
        _append_log(event.message, log_path=log_path)
        _notify_send(event.message)
        last_map[event.kind] = now_ts
        fired.append(event)

    if fired:
        state["last_alert"] = last_map
        _save_state(state_path, state)
    return fired
