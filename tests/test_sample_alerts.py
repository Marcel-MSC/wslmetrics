from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wslmetrics.alerts import dispatch_alerts, evaluate_alerts
from wslmetrics.config import AlertConfig
from wslmetrics.models import SystemSummary


def _cfg(**kwargs) -> AlertConfig:
    base = dict(
        mem_percent=90.0,
        cpu_percent=95.0,
        docker_mem_bytes=None,
        wsl_mem_percent=90.0,
        cooldown_seconds=300,
    )
    base.update(kwargs)
    return AlertConfig(**base)


def _summary(**kwargs) -> SystemSummary:
    base = dict(
        mem_total=8_000_000_000,
        mem_used=4_000_000_000,
        mem_available=4_000_000_000,
        mem_percent=50.0,
        cpu_percent=10.0,
        docker_mem_total=0,
        wsl_mem_limit=None,
        wsl_mem_percent=None,
    )
    base.update(kwargs)
    return SystemSummary(**base)


def test_evaluate_alerts_thresholds() -> None:
    events = evaluate_alerts(
        _summary(
            mem_percent=95.0,
            cpu_percent=99.0,
            docker_mem_total=1000,
            wsl_mem_limit=8_000_000_000,
            wsl_mem_percent=92.0,
        ),
        _cfg(docker_mem_bytes=500),
    )
    kinds = {e.kind for e in events}
    assert kinds == {"mem_percent", "cpu_percent", "docker_mem_bytes", "wsl_mem_percent"}


def test_evaluate_alerts_none_when_under() -> None:
    assert evaluate_alerts(_summary(), _cfg()) == []


def test_dispatch_alerts_cooldown(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "alerts.log"
    cfg = _cfg(cooldown_seconds=100)
    events = evaluate_alerts(_summary(mem_percent=99.0), cfg)
    with patch("wslmetrics.alerts._notify_send", return_value=False):
        first = dispatch_alerts(
            events, cfg, state_path=state, log_path=log, now=1_000.0
        )
        second = dispatch_alerts(
            events, cfg, state_path=state, log_path=log, now=1_050.0
        )
        third = dispatch_alerts(
            events, cfg, state_path=state, log_path=log, now=1_200.0
        )
    assert [e.kind for e in first] == ["mem_percent"]
    assert second == []
    assert [e.kind for e in third] == ["mem_percent"]
    assert log.exists()
    assert "RAM" in log.read_text(encoding="utf-8")
