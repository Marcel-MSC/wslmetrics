from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

import psutil

from wslmetrics.models import (
    DiskIO,
    DiskMount,
    GroupedProcessRow,
    NetIO,
    ProcessDetail,
    ProcessRow,
    SortKey,
    SystemSummary,
)


def _disk_mount(path: str) -> DiskMount | None:
    try:
        if not Path(path).exists():
            return None
        usage = psutil.disk_usage(path)
        return DiskMount(
            path=path,
            total=int(usage.total),
            used=int(usage.used),
            free=int(usage.free),
            percent=float(usage.percent),
        )
    except (OSError, PermissionError):
        return None


def _primary_net_io() -> NetIO | None:
    try:
        pernic = psutil.net_io_counters(pernic=True) or {}
    except (psutil.Error, OSError):
        return None
    if not pernic:
        return None
    if "eth0" in pernic:
        name = "eth0"
        c = pernic[name]
    else:
        candidates = [
            (n, c)
            for n, c in pernic.items()
            if n != "lo" and not n.startswith("lo")
        ]
        if not candidates:
            return None
        name, c = max(
            candidates, key=lambda item: int(item[1].bytes_recv) + int(item[1].bytes_sent)
        )
    return NetIO(
        iface=name,
        rx_bytes=int(c.bytes_recv),
        tx_bytes=int(c.bytes_sent),
    )


def _disk_io() -> DiskIO | None:
    try:
        counters = psutil.disk_io_counters()
    except (psutil.Error, OSError):
        return None
    if counters is None:
        return None
    return DiskIO(
        read_bytes=int(getattr(counters, "read_bytes", 0) or 0),
        write_bytes=int(getattr(counters, "write_bytes", 0) or 0),
    )


def _cpu_queue() -> tuple[float, float, float]:
    """Return 1m / 5m / 15m load averages (runnable + waiting tasks)."""
    try:
        a, b, c = os.getloadavg()
        return float(a), float(b), float(c)
    except (OSError, AttributeError):
        return 0.0, 0.0, 0.0


class HostCollector:
    """Collect host CPU/RAM and top processes via psutil."""

    def __init__(self, *, prime_processes: bool = True) -> None:
        # Prime cpu_percent so the first non-blocking sample is meaningful.
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._processes_primed = False
        if prime_processes:
            self._prime_processes()

    def _prime_processes(self) -> None:
        if self._processes_primed:
            return
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.Error, ProcessLookupError):
                continue
        self._processes_primed = True

    def summary(self) -> SystemSummary:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_queue_1m, cpu_queue_5m, cpu_queue_15m = _cpu_queue()
        mounts: list[DiskMount] = []
        for path in ("/", "/mnt/c"):
            m = _disk_mount(path)
            if m is not None:
                mounts.append(m)
        per_core = psutil.cpu_percent(interval=None, percpu=True) or []
        return SystemSummary(
            mem_total=mem.total,
            mem_used=mem.used,
            mem_available=mem.available,
            mem_percent=mem.percent,
            cpu_percent=psutil.cpu_percent(interval=None),
            swap_total=int(swap.total),
            swap_used=int(swap.used),
            swap_percent=float(swap.percent),
            cpu_queue_1m=cpu_queue_1m,
            cpu_queue_5m=cpu_queue_5m,
            cpu_queue_15m=cpu_queue_15m,
            disk_mounts=tuple(mounts),
            disk_io=_disk_io(),
            net_io=_primary_net_io(),
            cpu_per_core=tuple(float(x) for x in per_core),
        )

    def _iter_process_rows(self) -> list[ProcessRow]:
        rows: list[ProcessRow] = []
        for proc in psutil.process_iter(
            [
                "pid",
                "name",
                "username",
                "memory_info",
                "memory_percent",
                "ppid",
                "cmdline",
            ]
        ):
            try:
                info = proc.info
                mem_info = info.get("memory_info")
                if mem_info is None:
                    continue
                rss = int(getattr(mem_info, "rss", 0) or 0)
                cmdline_parts = info.get("cmdline") or []
                if isinstance(cmdline_parts, (list, tuple)):
                    cmdline = " ".join(str(p) for p in cmdline_parts if p)
                else:
                    cmdline = str(cmdline_parts)
                rows.append(
                    ProcessRow(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or "?"),
                        cpu_percent=float(proc.cpu_percent(interval=None) or 0.0),
                        mem_percent=float(info.get("memory_percent") or 0.0),
                        rss_bytes=rss,
                        username=str(info.get("username") or "?"),
                        ppid=int(info.get("ppid") or 0),
                        cmdline=cmdline,
                    )
                )
            except (psutil.Error, ProcessLookupError, TypeError, ValueError):
                continue
        return rows

    def processes(
        self,
        *,
        top: int = 30,
        sort_key: SortKey = SortKey.CPU,
    ) -> tuple[ProcessRow, ...]:
        rows = self._iter_process_rows()
        if sort_key is SortKey.MEM:
            rows.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
        else:
            rows.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)
        return tuple(rows[: max(1, top)])

    def grouped_processes(
        self,
        *,
        top: int = 30,
        sort_key: SortKey = SortKey.MEM,
        mem_total: int | None = None,
    ) -> tuple[GroupedProcessRow, ...]:
        rows = self._iter_process_rows()
        if mem_total is None:
            mem_total = int(psutil.virtual_memory().total) or 1

        buckets: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "cpu": 0.0, "rss": 0, "users": set()}
        )
        for row in rows:
            b = buckets[row.name]
            b["count"] += 1
            b["cpu"] += row.cpu_percent
            b["rss"] += row.rss_bytes
            b["users"].add(row.username)

        grouped: list[GroupedProcessRow] = []
        for name, b in buckets.items():
            users = b["users"]
            username = next(iter(users)) if len(users) == 1 else "*"
            rss = int(b["rss"])
            grouped.append(
                GroupedProcessRow(
                    name=name,
                    count=int(b["count"]),
                    cpu_percent=float(b["cpu"]),
                    mem_percent=(rss / mem_total) * 100.0 if mem_total else 0.0,
                    rss_bytes=rss,
                    username=username,
                )
            )

        if sort_key is SortKey.CPU:
            grouped.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)
        else:
            grouped.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
        return tuple(grouped[: max(1, top)])

    def process_detail(self, pid: int) -> ProcessDetail | None:
        """Fresh one-shot detail for a PID (parent + children)."""
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                name = proc.name() or "?"
                username = proc.username() or "?"
                mem_info = proc.memory_info()
                rss = int(getattr(mem_info, "rss", 0) or 0)
                mem_percent = float(proc.memory_percent() or 0.0)
                cpu_percent = float(proc.cpu_percent(interval=None) or 0.0)
                ppid = int(proc.ppid() or 0)
                cmdline_parts = proc.cmdline() or []
                cmdline = " ".join(str(p) for p in cmdline_parts if p)
        except (psutil.Error, ProcessLookupError):
            return None

        parent_name = "?"
        if ppid > 0:
            try:
                parent_name = psutil.Process(ppid).name() or "?"
            except (psutil.Error, ProcessLookupError):
                parent_name = "?"

        children: list[tuple[int, str]] = []
        try:
            for child in psutil.Process(pid).children(recursive=False):
                try:
                    children.append((int(child.pid), child.name() or "?"))
                except (psutil.Error, ProcessLookupError):
                    continue
        except (psutil.Error, ProcessLookupError):
            pass
        children.sort(key=lambda c: c[0])

        return ProcessDetail(
            pid=pid,
            name=name,
            username=username,
            cpu_percent=cpu_percent,
            mem_percent=mem_percent,
            rss_bytes=rss,
            ppid=ppid,
            parent_name=parent_name,
            cmdline=cmdline,
            children=tuple(children),
        )

    def pids_by_name(self, name: str, *, limit: int = 50) -> tuple[tuple[int, str], ...]:
        """List (pid, cmdline-or-name) matching process name."""
        matches: list[tuple[int, str]] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                if str(info.get("name") or "") != name:
                    continue
                parts = info.get("cmdline") or []
                if isinstance(parts, (list, tuple)):
                    cmd = " ".join(str(p) for p in parts if p)
                else:
                    cmd = str(parts)
                matches.append((int(info["pid"]), cmd or name))
            except (psutil.Error, ProcessLookupError, TypeError, ValueError):
                continue
            if len(matches) >= limit:
                break
        matches.sort(key=lambda m: m[0])
        return tuple(matches)

    @staticmethod
    def terminate_process(pid: int) -> None:
        """Send SIGTERM to pid. Raises psutil.Error on failure."""
        psutil.Process(pid).terminate()

    @staticmethod
    def _kill_permission_error(pid: int, proc: psutil.Process | None) -> RuntimeError:
        owner = "?"
        name = "?"
        if proc is not None:
            try:
                owner = proc.username() or "?"
            except (psutil.Error, ProcessLookupError):
                pass
            try:
                name = proc.name() or "?"
            except (psutil.Error, ProcessLookupError):
                pass
        return RuntimeError(
            f"permission denied for PID {pid} ({name}, user={owner}) — "
            "use Kill with sudo (K) if you have sudo rights"
        )

    @staticmethod
    def kill_process(pid: int, *, grace_seconds: float = 2.0) -> str:
        """SIGTERM, wait up to grace_seconds, then SIGKILL if still alive.

        Returns one of: ``"term"``, ``"kill"``, ``"already_gone"``.
        Raises RuntimeError / psutil.Error on failure.
        """
        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return "already_gone"

        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            return "already_gone"
        except psutil.AccessDenied as exc:
            raise HostCollector._kill_permission_error(pid, proc) from exc

        _gone, alive = psutil.wait_procs([proc], timeout=max(0.1, grace_seconds))
        if not alive:
            return "term"

        try:
            proc.kill()
        except psutil.NoSuchProcess:
            return "term"
        except psutil.AccessDenied as exc:
            raise HostCollector._kill_permission_error(pid, proc) from exc

        _gone2, alive2 = psutil.wait_procs([proc], timeout=1.0)
        if alive2:
            raise RuntimeError(f"PID {pid} still alive after SIGKILL")
        return "kill"

    @staticmethod
    def _sudo_run(
        args: list[str],
        *,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command under sudo. If password is set, use ``sudo -S``."""
        if password is not None:
            cmd = ["sudo", "-S", "-p", "", *args]
            return subprocess.run(
                cmd,
                input=password + "\n",
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        cmd = ["sudo", "-n", *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def reset_sudo_timestamp() -> None:
        """Drop cached sudo credentials (``sudo -k``). Does not need a password."""
        subprocess.run(
            ["sudo", "-k"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    @staticmethod
    def verify_sudo_password(password: str) -> None:
        """Validate sudo password via ``sudo -k`` then ``sudo -S -v``.

        Always resets the timestamp first so a wrong password cannot succeed
        by riding on a previous sudo authentication in the same session.
        """
        if not password:
            raise RuntimeError("sudo password is empty")
        HostCollector.reset_sudo_timestamp()
        auth = HostCollector._sudo_run(["-v"], password=password, timeout=45.0)
        if auth.returncode == 0:
            return
        # Failed auth — keep timestamp clear.
        HostCollector.reset_sudo_timestamp()
        err = (auth.stderr or auth.stdout or "").strip()
        low = err.lower()
        if (
            "try again" in low
            or "incorrect" in low
            or "sorry" in low
            or auth.returncode != 0
        ):
            # Prefer a clear wrong-password message when sudo rejected auth.
            if "no new privileges" in low:
                raise RuntimeError(
                    "sudo blocked in this environment (no new privileges)"
                )
            if auth.returncode != 0:
                raise RuntimeError("wrong password")
        raise RuntimeError(
            f"sudo authentication failed: {err or f'exit {auth.returncode}'}"
        )

    @staticmethod
    def kill_process_sudo(
        pid: int,
        password: str,
        *,
        grace_seconds: float = 2.0,
        already_authenticated: bool = False,
    ) -> str:
        """Authenticate with sudo, then SIGTERM → wait → SIGKILL.

        Kill steps always use ``sudo -S`` with the provided password (never
        ``sudo -n`` alone), so a failed/wrong password cannot signal a PID.
        Returns one of: ``"term"``, ``"kill"``, ``"already_gone"``.
        """
        if not password:
            raise RuntimeError("sudo password is empty")
        if not already_authenticated:
            HostCollector.verify_sudo_password(password)
        else:
            # Modal already verified; ensure we still have a warm stamp for -S.
            # Do not call -k here — that would invalidate the just-checked auth.
            pass

        def _sudo_kill(sig: str) -> subprocess.CompletedProcess[str]:
            # Pass password on every kill so we never depend on a stale cache
            # that might have existed before this call.
            return HostCollector._sudo_run(
                ["kill", f"-{sig}", str(pid)],
                password=password,
                timeout=15.0,
            )

        try:
            if not psutil.pid_exists(pid):
                return "already_gone"

            term = _sudo_kill("TERM")
            if term.returncode != 0 and not psutil.pid_exists(pid):
                return "already_gone"
            if term.returncode != 0:
                err = (term.stderr or term.stdout or "").strip()
                raise RuntimeError(
                    f"sudo kill -TERM {pid} failed: "
                    f"{err or f'exit {term.returncode}'}"
                )

            try:
                proc = psutil.Process(pid)
            except psutil.NoSuchProcess:
                return "term"

            _gone, alive = psutil.wait_procs(
                [proc], timeout=max(0.1, grace_seconds)
            )
            if not alive:
                return "term"

            kill = _sudo_kill("KILL")
            if kill.returncode != 0 and not psutil.pid_exists(pid):
                return "term"
            if kill.returncode != 0:
                err = (kill.stderr or kill.stdout or "").strip()
                raise RuntimeError(
                    f"sudo kill -KILL {pid} failed: "
                    f"{err or f'exit {kill.returncode}'}"
                )

            if psutil.pid_exists(pid):
                _gone2, alive2 = psutil.wait_procs([proc], timeout=1.0)
                if alive2 or psutil.pid_exists(pid):
                    raise RuntimeError(f"PID {pid} still alive after sudo SIGKILL")
            return "kill"
        finally:
            # Avoid leaving an elevated sudo stamp after a kill attempt.
            HostCollector.reset_sudo_timestamp()
