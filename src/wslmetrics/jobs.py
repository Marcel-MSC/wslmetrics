from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from wslmetrics.config import load_config
from wslmetrics.paths import (
    ALERTS_LOG,
    DB_PATH,
    STATE_DIR,
    SYSTEMD_USER_DIR,
    ensure_dirs,
)
from wslmetrics.storage import MetricsStore

SERVICE_NAME = "wslmetrics-sample.service"
TIMER_NAME = "wslmetrics-sample.timer"


def _resolve_executable() -> str:
    # Prefer the console script next to the current interpreter.
    candidate = Path(sys.executable).resolve().parent / "wslmetrics"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    which = shutil.which("wslmetrics")
    if which:
        return which
    return f"{sys.executable} -m wslmetrics"


def _unit_text(name: str, executable: str, interval: int) -> str:
    if name == SERVICE_NAME:
        return f"""[Unit]
Description=wslmetrics metrics sample
After=network.target

[Service]
Type=oneshot
ExecStart={executable} sample
Nice=10

[Install]
WantedBy=default.target
"""
    return f"""[Unit]
Description=Run wslmetrics sample periodically

[Timer]
OnBootSec=30
OnUnitActiveSec={interval}
AccuracySec=5
Persistent=true
Unit={SERVICE_NAME}

[Install]
WantedBy=timers.target
"""


def _run_systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["systemctl", "--user", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=["systemctl", "--user", *args],
            returncode=127,
            stdout="",
            stderr="systemctl not found",
        )


def install_job() -> str:
    ensure_dirs()
    cfg = load_config()
    executable = _resolve_executable()
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    service_path = SYSTEMD_USER_DIR / SERVICE_NAME
    timer_path = SYSTEMD_USER_DIR / TIMER_NAME
    service_path.write_text(
        _unit_text(SERVICE_NAME, executable, cfg.sample_interval_seconds),
        encoding="utf-8",
    )
    timer_path.write_text(
        _unit_text(TIMER_NAME, executable, cfg.sample_interval_seconds),
        encoding="utf-8",
    )

    reload = _run_systemctl("daemon-reload")
    enable = _run_systemctl("enable", "--now", TIMER_NAME)
    if reload.returncode != 0 or enable.returncode != 0:
        err = (reload.stderr or enable.stderr or "systemctl failed").strip()
        # Still leave unit files in place so the user can enable after enabling systemd.
        raise RuntimeError(
            "Wrote unit files but failed to enable the systemd user timer.\n"
            "Enable systemd in WSL (/etc/wsl.conf → [boot] systemd=true), "
            "then: wsl --shutdown, reopen, and re-run install-job.\n"
            f"Units written to:\n  {service_path}\n  {timer_path}\n"
            f"systemctl error: {err}"
        )
    return (
        f"Installed {TIMER_NAME} (every {cfg.sample_interval_seconds}s)\n"
        f"ExecStart: {executable} sample\n"
        f"Units: {service_path}\n         {timer_path}"
    )


def uninstall_job() -> str:
    _run_systemctl("disable", "--now", TIMER_NAME)
    service_path = SYSTEMD_USER_DIR / SERVICE_NAME
    timer_path = SYSTEMD_USER_DIR / TIMER_NAME
    for path in (service_path, timer_path):
        if path.exists():
            path.unlink()
    _run_systemctl("daemon-reload")
    return f"Removed {TIMER_NAME} and {SERVICE_NAME}"


def status_report() -> str:
    ensure_dirs()
    lines: list[str] = []

    timer = _run_systemctl("is-active", TIMER_NAME)
    enabled = _run_systemctl("is-enabled", TIMER_NAME)
    lines.append(
        f"timer: active={timer.stdout.strip() or 'unknown'} "
        f"enabled={enabled.stdout.strip() or 'unknown'}"
    )

    show = _run_systemctl(
        "show",
        TIMER_NAME,
        "--property=LastTriggerUSec",
        "--property=NextElapseUSecRealtime",
    )
    if show.returncode == 0 and show.stdout.strip():
        lines.append(show.stdout.strip())

    with MetricsStore() as store:
        latest = store.latest_ts()
    if latest:
        iso = datetime.fromtimestamp(latest, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        age = int(time.time()) - latest
        lines.append(f"last_sample: {iso} ({age}s ago)")
    else:
        lines.append("last_sample: none")

    lines.append(f"db: {DB_PATH} ({'exists' if DB_PATH.exists() else 'missing'})")
    lines.append(f"state_dir: {STATE_DIR}")

    if ALERTS_LOG.exists():
        try:
            tail = ALERTS_LOG.read_text(encoding="utf-8").splitlines()[-5:]
        except OSError:
            tail = []
        if tail:
            lines.append("recent_alerts:")
            lines.extend(f"  {line}" for line in tail)
        else:
            lines.append("recent_alerts: (empty)")
    else:
        lines.append("recent_alerts: (no log yet)")

    return "\n".join(lines)
