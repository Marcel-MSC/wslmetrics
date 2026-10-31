from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

from wslmetrics.collectors.host import HostCollector
from wslmetrics.models import SortKey


def _fake_proc(
    pid: int,
    name: str,
    rss: int,
    cpu: float,
    user: str = "u",
    *,
    ppid: int = 1,
    cmdline: list[str] | None = None,
) -> MagicMock:
    proc = MagicMock()
    proc.info = {
        "pid": pid,
        "name": name,
        "username": user,
        "memory_info": SimpleNamespace(rss=rss),
        "memory_percent": (rss / (8 * 1024**3)) * 100,
        "ppid": ppid,
        "cmdline": cmdline if cmdline is not None else [name],
    }
    proc.cpu_percent.return_value = cpu
    return proc


@patch("wslmetrics.collectors.host.psutil.virtual_memory")
@patch("wslmetrics.collectors.host.psutil.cpu_percent", return_value=0.0)
@patch("wslmetrics.collectors.host.psutil.process_iter")
def test_processes_and_grouped(
    mock_iter: MagicMock,
    _cpu: MagicMock,
    mock_vm: MagicMock,
) -> None:
    mock_vm.return_value = SimpleNamespace(
        total=8 * 1024**3, used=1, available=1, percent=1.0
    )
    procs = [
        _fake_proc(1, "chrome", 100_000_000, 10.0),
        _fake_proc(2, "chrome", 50_000_000, 5.0),
        _fake_proc(3, "node", 200_000_000, 40.0),
    ]
    mock_iter.return_value = procs

    host = HostCollector(prime_processes=False)
    by_cpu = host.processes(top=10, sort_key=SortKey.CPU)
    assert by_cpu[0].name == "node"
    assert len(by_cpu) == 3

    grouped = host.grouped_processes(
        top=10, sort_key=SortKey.MEM, mem_total=8 * 1024**3
    )
    chrome = next(g for g in grouped if g.name == "chrome")
    assert chrome.count == 2
    assert chrome.rss_bytes == 150_000_000
    assert chrome.cpu_percent == 15.0
    assert grouped[0].name == "node"  # highest rss


@patch("wslmetrics.collectors.host._primary_net_io")
@patch("wslmetrics.collectors.host._disk_io")
@patch("wslmetrics.collectors.host._disk_mount")
@patch("wslmetrics.collectors.host._cpu_queue", return_value=(0.5, 0.7, 0.9))
@patch("wslmetrics.collectors.host.psutil.swap_memory")
@patch("wslmetrics.collectors.host.psutil.virtual_memory")
@patch("wslmetrics.collectors.host.psutil.cpu_percent")
def test_summary_disk_load_net(
    mock_cpu: MagicMock,
    mock_vm: MagicMock,
    mock_swap: MagicMock,
    _load: MagicMock,
    mock_mount: MagicMock,
    mock_disk_io: MagicMock,
    mock_net: MagicMock,
) -> None:
    from wslmetrics.models import DiskIO, DiskMount, NetIO

    mock_cpu.return_value = 0.0
    mock_vm.return_value = SimpleNamespace(
        total=8 * 1024**3, used=2 * 1024**3, available=6 * 1024**3, percent=25.0
    )
    mock_swap.return_value = SimpleNamespace(total=0, used=0, percent=0.0)

    def mount_for(path: str):
        return DiskMount(path=path, total=1000, used=400, free=600, percent=40.0)

    mock_mount.side_effect = mount_for
    mock_disk_io.return_value = DiskIO(read_bytes=111, write_bytes=222)
    mock_net.return_value = NetIO(iface="eth0", rx_bytes=1000, tx_bytes=2000)

    host = HostCollector(prime_processes=False)
    mock_cpu.side_effect = [[11.0, 22.0], 15.0]
    s = host.summary()
    assert s.cpu_queue_1m == 0.5
    assert s.cpu_queue_5m == 0.7
    assert s.cpu_queue_15m == 0.9
    assert len(s.disk_mounts) == 2
    assert {m.path for m in s.disk_mounts} == {"/", "/mnt/c"}
    assert s.disk_io is not None and s.disk_io.read_bytes == 111
    assert s.net_io is not None and s.net_io.iface == "eth0"
    assert s.cpu_per_core == (11.0, 22.0)
    assert s.cpu_percent == 15.0


@patch("wslmetrics.collectors.host.psutil.virtual_memory")
@patch("wslmetrics.collectors.host.psutil.cpu_percent", return_value=0.0)
@patch("wslmetrics.collectors.host.psutil.process_iter")
def test_processes_include_ppid_cmdline(
    mock_iter: MagicMock,
    _cpu: MagicMock,
    mock_vm: MagicMock,
) -> None:
    mock_vm.return_value = SimpleNamespace(
        total=8 * 1024**3, used=1, available=1, percent=1.0
    )
    mock_iter.return_value = [
        _fake_proc(
            42, "python", 10_000, 1.0, ppid=7, cmdline=["python", "-m", "wslmetrics"]
        )
    ]
    host = HostCollector(prime_processes=False)
    rows = host.processes(top=5)
    assert rows[0].ppid == 7
    assert rows[0].cmdline == "python -m wslmetrics"


@patch("wslmetrics.collectors.host.psutil.Process")
def test_terminate_process(mock_proc_cls: MagicMock) -> None:
    proc = MagicMock()
    mock_proc_cls.return_value = proc
    HostCollector.terminate_process(99)
    mock_proc_cls.assert_called_once_with(99)
    proc.terminate.assert_called_once()


@patch("wslmetrics.collectors.host.psutil.wait_procs")
@patch("wslmetrics.collectors.host.psutil.Process")
def test_kill_process_stops_on_term(
    mock_proc_cls: MagicMock, mock_wait: MagicMock
) -> None:
    proc = MagicMock()
    mock_proc_cls.return_value = proc
    mock_wait.return_value = ([proc], [])
    assert HostCollector.kill_process(99, grace_seconds=0.1) == "term"
    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()


@patch("wslmetrics.collectors.host.psutil.wait_procs")
@patch("wslmetrics.collectors.host.psutil.Process")
def test_kill_process_escalates_to_sigkill(
    mock_proc_cls: MagicMock, mock_wait: MagicMock
) -> None:
    proc = MagicMock()
    mock_proc_cls.return_value = proc
    mock_wait.side_effect = [
        ([], [proc]),  # still alive after TERM
        ([proc], []),  # gone after KILL
    ]
    assert HostCollector.kill_process(99, grace_seconds=0.1) == "kill"
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


@patch(
    "wslmetrics.collectors.host.psutil.Process",
    side_effect=psutil.NoSuchProcess(99),
)
def test_kill_process_already_gone(_mock: MagicMock) -> None:
    assert HostCollector.kill_process(99) == "already_gone"


@patch("wslmetrics.collectors.host.psutil.Process")
def test_kill_process_access_denied_message(mock_proc_cls: MagicMock) -> None:
    proc = MagicMock()
    proc.username.return_value = "root"
    proc.name.return_value = "systemd"
    proc.terminate.side_effect = psutil.AccessDenied(1)
    mock_proc_cls.return_value = proc
    try:
        HostCollector.kill_process(1, grace_seconds=0.1)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        msg = str(exc)
        assert "permission denied" in msg
        assert "PID 1" in msg
        assert "sudo" in msg


@patch("wslmetrics.collectors.host.HostCollector.reset_sudo_timestamp")
@patch("wslmetrics.collectors.host.psutil.pid_exists")
@patch("wslmetrics.collectors.host.psutil.wait_procs")
@patch("wslmetrics.collectors.host.psutil.Process")
@patch("wslmetrics.collectors.host.HostCollector._sudo_run")
def test_kill_process_sudo_term(
    mock_sudo: MagicMock,
    mock_proc_cls: MagicMock,
    mock_wait: MagicMock,
    mock_exists: MagicMock,
    _reset: MagicMock,
) -> None:
    from types import SimpleNamespace

    mock_sudo.side_effect = [
        SimpleNamespace(returncode=0, stderr="", stdout=""),  # -v
        SimpleNamespace(returncode=0, stderr="", stdout=""),  # TERM with -S
    ]
    mock_exists.return_value = True
    proc = MagicMock()
    mock_proc_cls.return_value = proc
    mock_wait.return_value = ([proc], [])
    assert HostCollector.kill_process_sudo(99, "secret", grace_seconds=0.1) == "term"
    assert mock_sudo.call_count == 2
    # Kill must pass password (sudo -S), never rely on sudo -n alone.
    term_call = mock_sudo.call_args_list[1]
    assert term_call.kwargs.get("password") == "secret"


@patch("wslmetrics.collectors.host.HostCollector.reset_sudo_timestamp")
@patch("wslmetrics.collectors.host.HostCollector._sudo_run")
def test_verify_sudo_resets_timestamp_before_auth(
    mock_sudo: MagicMock, mock_reset: MagicMock
) -> None:
    from types import SimpleNamespace

    mock_sudo.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
    HostCollector.verify_sudo_password("secret")
    mock_reset.assert_called()
    mock_sudo.assert_called_once()
    assert mock_sudo.call_args.kwargs.get("password") == "secret"


@patch("wslmetrics.collectors.host.HostCollector._sudo_run")
def test_verify_sudo_password_bad(mock_sudo: MagicMock) -> None:
    from types import SimpleNamespace

    mock_sudo.return_value = SimpleNamespace(
        returncode=1, stderr="Sorry, try again.", stdout=""
    )
    try:
        HostCollector.verify_sudo_password("wrong")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "wrong password" in str(exc).lower()


def test_verify_sudo_password_empty() -> None:
    try:
        HostCollector.verify_sudo_password("")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "empty" in str(exc).lower()


def test_sudo_password_screen_attempt_limit() -> None:
    assert __import__(
        "wslmetrics.process_detail", fromlist=["SudoPasswordScreen"]
    ).SudoPasswordScreen.MAX_ATTEMPTS == 3


@patch("wslmetrics.collectors.host.psutil.Process")
def test_process_detail(mock_proc_cls: MagicMock) -> None:
    proc = MagicMock()
    proc.name.return_value = "node"
    proc.username.return_value = "u"
    proc.memory_info.return_value = SimpleNamespace(rss=1234)
    proc.memory_percent.return_value = 1.5
    proc.cpu_percent.return_value = 2.5
    proc.ppid.return_value = 1
    proc.cmdline.return_value = ["node", "app.js"]
    proc.children.return_value = []
    oneshot = MagicMock()
    oneshot.__enter__ = MagicMock(return_value=None)
    oneshot.__exit__ = MagicMock(return_value=False)
    proc.oneshot.return_value = oneshot

    parent = MagicMock()
    parent.name.return_value = "init"

    def factory(pid: int):
        if pid == 99:
            return proc
        if pid == 1:
            return parent
        raise ProcessLookupError(pid)

    mock_proc_cls.side_effect = factory

    host = HostCollector(prime_processes=False)
    detail = host.process_detail(99)
    assert detail is not None
    assert detail.pid == 99
    assert detail.parent_name == "init"
    assert "app.js" in detail.cmdline
