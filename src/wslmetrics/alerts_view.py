from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from wslmetrics.alerts import _load_state
from wslmetrics.config import AlertConfig, AppConfig, load_config
from wslmetrics.formatting import format_bytes, format_percent
from wslmetrics.paths import ALERT_STATE_PATH, ALERTS_LOG

ALERT_KINDS = (
    "mem_percent",
    "cpu_percent",
    "docker_mem_bytes",
    "wsl_mem_percent",
    "swap_percent",
)


def _format_threshold(kind: str, cfg: AlertConfig) -> str:
    if kind == "mem_percent":
        return format_percent(cfg.mem_percent)
    if kind == "cpu_percent":
        return format_percent(cfg.cpu_percent)
    if kind == "docker_mem_bytes":
        if cfg.docker_mem_bytes is None:
            return "off"
        return format_bytes(cfg.docker_mem_bytes)
    if kind == "wsl_mem_percent":
        if cfg.wsl_mem_percent is None:
            return "off"
        return format_percent(cfg.wsl_mem_percent)
    if kind == "swap_percent":
        if cfg.swap_percent is None:
            return "off"
        return format_percent(cfg.swap_percent)
    return "?"


def _format_ts(ts: float | None) -> str:
    if ts is None or ts <= 0:
        return "never"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _cooldown_remaining(
    last_ts: float | None, *, cooldown: int, now: float
) -> str:
    if last_ts is None or last_ts <= 0:
        return "-"
    remaining = cooldown - (now - last_ts)
    if remaining <= 0:
        return "ready"
    return f"{int(remaining)}s"


def has_hot_alert(
    *,
    cfg: AppConfig | None = None,
    state_path: Path = ALERT_STATE_PATH,
    now: float | None = None,
) -> bool:
    """True if any kind fired within the cooldown window."""
    app_cfg = cfg or load_config()
    now_ts = float(now if now is not None else time.time())
    state = _load_state(state_path)
    last_map = state.get("last_alert") if isinstance(state.get("last_alert"), dict) else {}
    cooldown = app_cfg.alerts.cooldown_seconds
    for kind in ALERT_KINDS:
        last = float(last_map.get(kind, 0) or 0)
        if last > 0 and now_ts - last < cooldown:
            return True
    return False


def tail_alert_log(
    *,
    log_path: Path = ALERTS_LOG,
    limit: int = 50,
) -> list[str]:
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return lines[-max(1, limit) :]


def render_alerts_text(
    *,
    cfg: AppConfig | None = None,
    state_path: Path = ALERT_STATE_PATH,
    log_path: Path = ALERTS_LOG,
    now: float | None = None,
    log_limit: int = 50,
) -> str:
    app_cfg = cfg or load_config()
    now_ts = float(now if now is not None else time.time())
    state = _load_state(state_path)
    last_map = state.get("last_alert") if isinstance(state.get("last_alert"), dict) else {}
    cooldown = app_cfg.alerts.cooldown_seconds

    lines: list[str] = [
        f"Thresholds · cooldown={cooldown}s",
        f"  {'KIND':<18} {'THRESHOLD':>12} {'LAST FIRE':>22} {'COOLDOWN':>10}",
        "  " + "-" * 66,
    ]
    for kind in ALERT_KINDS:
        last = float(last_map.get(kind, 0) or 0) or None
        lines.append(
            f"  {kind:<18} {_format_threshold(kind, app_cfg.alerts):>12} "
            f"{_format_ts(last):>22} "
            f"{_cooldown_remaining(last, cooldown=cooldown, now=now_ts):>10}"
        )

    lines.append("")
    lines.append(f"Recent alerts (last {log_limit})")
    lines.append("  " + "-" * 66)
    recent = tail_alert_log(log_path=log_path, limit=log_limit)
    if not recent:
        lines.append("  (no alerts logged yet)")
    else:
        for line in recent:
            lines.append(f"  {line}")
    return "\n".join(lines).rstrip() + "\n"
