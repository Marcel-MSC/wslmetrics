from __future__ import annotations

import os
from pathlib import Path


def _xdg_home(env_var: str, default_under_home: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value).expanduser()
    return Path.home() / default_under_home


STATE_DIR = _xdg_home("XDG_STATE_HOME", ".local/state") / "wslmetrics"
CONFIG_DIR = _xdg_home("XDG_CONFIG_HOME", ".config") / "wslmetrics"
CONFIG_PATH = CONFIG_DIR / "config.yaml"
DB_PATH = STATE_DIR / "metrics.db"
ALERTS_LOG = STATE_DIR / "alerts.log"
ALERT_STATE_PATH = STATE_DIR / "alert_state.json"
PROM_TEXTFILE = STATE_DIR / "metrics.prom"
SYSTEMD_USER_DIR = _xdg_home("XDG_CONFIG_HOME", ".config") / "systemd" / "user"


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
