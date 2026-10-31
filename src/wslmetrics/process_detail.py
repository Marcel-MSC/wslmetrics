from __future__ import annotations

import threading
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from wslmetrics.collectors.host import HostCollector
from wslmetrics.formatting import format_bytes, format_percent
from wslmetrics.models import ProcessDetail

KillMode = Literal["term", "sudo"]

_KILL_LEGEND = """\
Kill options — choose carefully
  SIGTERM (k)   polite stop as your user · only PIDs you own · may escalate to SIGKILL after ~2s
  sudo kill (K) elevated · can stop other users / system PIDs · asks password · higher risk"""

_CONFIRM_TERM_LEGEND = """\
SIGTERM — lower risk
  Sends SIGTERM as your user. If the process ignores it, escalates to SIGKILL after ~2s.
  Cannot stop processes owned by root/other users (permission denied)."""

_CONFIRM_SUDO_LEGEND = """\
sudo kill — higher risk
  Uses sudo (password) to signal any PID: SIGTERM, wait ~2s, then SIGKILL if still alive.
  Can stop system services and other users' processes. Prefer SIGTERM when the PID is yours."""


def _format_detail(detail: ProcessDetail) -> str:
    cmdline = detail.cmdline or "(empty)"
    lines = [
        f"PID {detail.pid}  ·  {detail.name}  ·  {detail.username}",
        f"CPU {format_percent(detail.cpu_percent)}  ·  "
        f"MEM {format_percent(detail.mem_percent)}  ·  "
        f"RSS {format_bytes(detail.rss_bytes)}",
        f"Parent  {detail.ppid} ({detail.parent_name})",
        "",
        "Cmdline",
        f"  {cmdline}",
        "",
        f"Children ({len(detail.children)})",
    ]
    if not detail.children:
        lines.append("  (none)")
    else:
        for cpid, cname in detail.children[:40]:
            lines.append(f"  {cpid:>7}  {cname}")
        if len(detail.children) > 40:
            lines.append(f"  … +{len(detail.children) - 40} more")
    return "\n".join(lines)


def _format_group(name: str, matches: tuple[tuple[int, str], ...]) -> str:
    lines = [
        f"Group  {name}  ·  {len(matches)} process(es)",
        "",
        f"  {'PID':>7}  CMDLINE",
        "  " + "-" * 60,
    ]
    for pid, cmd in matches[:50]:
        shown = (cmd[:70] + "…") if len(cmd) > 70 else cmd
        lines.append(f"  {pid:>7}  {shown}")
    if len(matches) > 50:
        lines.append(f"  … +{len(matches) - 50} more")
    lines.append("")
    lines.append("(open a PID row with GROUP OFF to kill a single process)")
    return "\n".join(lines)


_KILL_RESULT_MSG = {
    "term": "stopped with SIGTERM",
    "kill": "stopped with SIGKILL (after SIGTERM wait)",
    "already_gone": "already gone",
}


class ConfirmKillScreen(ModalScreen[bool]):
    """Confirm a specific kill mode (SIGTERM or sudo)."""

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=True),
        Binding("n", "cancel", "No", show=True),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    CSS = """
    ConfirmKillScreen {
        align: center middle;
    }
    #kill-dialog {
        width: 76;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #kill-legend {
        margin: 1 0;
        color: $text-muted;
    }
    #kill-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, pid: int, name: str, *, mode: KillMode) -> None:
        super().__init__()
        self._pid = pid
        self._name = name
        self._mode = mode

    def compose(self) -> ComposeResult:
        title = (
            f"SIGTERM PID {self._pid} ({self._name})?"
            if self._mode == "term"
            else f"sudo kill PID {self._pid} ({self._name})?"
        )
        legend = (
            _CONFIRM_TERM_LEGEND if self._mode == "term" else _CONFIRM_SUDO_LEGEND
        )
        border_note = "Press y to confirm, n/Esc to cancel."
        with Vertical(id="kill-dialog"):
            yield Label(title)
            yield Static(legend, id="kill-legend")
            yield Label(border_note)
            with Horizontal(id="kill-actions"):
                if self._mode == "term":
                    yield Button("SIGTERM (y)", variant="error", id="btn-yes")
                else:
                    yield Button("sudo kill (y)", variant="warning", id="btn-yes")
                yield Button("Cancel (n)", variant="primary", id="btn-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class SudoPasswordScreen(ModalScreen[str | None]):
    """Ask for sudo password (masked). Stays open on wrong password (max 3 tries)."""

    MAX_ATTEMPTS = 3

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    CSS = """
    SudoPasswordScreen {
        align: center middle;
    }
    #sudo-dialog {
        width: 66;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #sudo-legend {
        margin: 1 0;
        color: $text-muted;
    }
    #sudo-status {
        margin: 1 0;
        color: $error;
        height: auto;
    }
    #sudo-pass {
        width: 1fr;
        margin-top: 1;
    }
    #sudo-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    """

    def __init__(self, pid: int, name: str) -> None:
        super().__init__()
        self._pid = pid
        self._name = name
        self._attempts = 0
        self._locked = False
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="sudo-dialog"):
            yield Label(f"sudo password — kill PID {self._pid} ({self._name})")
            yield Static(
                "Password is used only for this kill (sudo -S) and is not stored.\n"
                f"Wrong password: modal stays open · {self.MAX_ATTEMPTS} attempts.",
                id="sudo-legend",
            )
            yield Static("", id="sudo-status")
            yield Label("Enter submits  ·  Esc cancels", id="sudo-hint")
            yield Input(
                placeholder="password",
                password=True,
                id="sudo-pass",
            )
            with Horizontal(id="sudo-actions"):
                yield Button("OK", variant="warning", id="btn-ok")
                yield Button("Cancel — back to TUI", variant="primary", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#sudo-pass", Input).focus()

    def _set_status(self, message: str) -> None:
        self.query_one("#sudo-status", Static).update(message)

    def _lock_out(self) -> None:
        self._locked = True
        inp = self.query_one("#sudo-pass", Input)
        inp.value = ""
        inp.disabled = True
        inp.display = False
        ok = self.query_one("#btn-ok", Button)
        ok.display = False
        self.query_one("#sudo-hint", Label).update(
            "Attempts exhausted — press Cancel to return to the TUI."
        )
        self._set_status(
            f"Wrong password ({self.MAX_ATTEMPTS}/{self.MAX_ATTEMPTS}). "
            "Only Cancel is available."
        )
        self.query_one("#btn-cancel", Button).focus()

    def _try_password(self, password: str) -> None:
        if self._locked or self._busy:
            return
        if password == "":
            self._set_status("Empty password — try again.")
            return

        self._busy = True
        self._set_status("Checking sudo password…")
        try:
            HostCollector.verify_sudo_password(password)
        except RuntimeError as exc:
            self._busy = False
            self._attempts += 1
            left = self.MAX_ATTEMPTS - self._attempts
            inp = self.query_one("#sudo-pass", Input)
            inp.value = ""
            if left <= 0:
                self._lock_out()
                return
            self._set_status(
                f"Wrong password ({self._attempts}/{self.MAX_ATTEMPTS}). "
                f"{left} attempt(s) left."
            )
            inp.focus()
            return
        except Exception as exc:  # noqa: BLE001
            self._busy = False
            self._set_status(f"sudo check failed: {exc}")
            self.query_one("#sudo-pass", Input).value = ""
            self.query_one("#sudo-pass", Input).focus()
            return

        self._busy = False
        # Auth OK — credentials cached for the following sudo -n kill.
        self.dismiss(password)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "sudo-pass" and not self._locked:
            self._try_password(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ok" and not self._locked:
            self._try_password(self.query_one("#sudo-pass", Input).value)
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ProcessDetailScreen(ModalScreen[None]):
    """Show process cmdline / parent / children; optional kill."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("q", "close", "Close", show=False),
        Binding("k", "kill", "SIGTERM", show=True),
        Binding("K", "kill_sudo", "sudo kill", show=True),
    ]

    CSS = """
    ProcessDetailScreen {
        align: center middle;
    }
    #detail-dialog {
        width: 84;
        max-width: 100%;
        height: auto;
        max-height: 80%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
        overflow-y: auto;
    }
    #kill-legend {
        margin-top: 1;
        color: $text-muted;
        height: auto;
    }
    #detail-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #detail-body {
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        detail: ProcessDetail | None = None,
        group_name: str | None = None,
        group_matches: tuple[tuple[int, str], ...] = (),
    ) -> None:
        super().__init__()
        self._detail = detail
        self._group_name = group_name
        self._group_matches = group_matches

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-dialog"):
            if self._detail is not None:
                body = _format_detail(self._detail)
            elif self._group_name is not None:
                body = _format_group(self._group_name, self._group_matches)
            else:
                body = "(no process selected)"
            yield Static(body, id="detail-body")
            if self._detail is not None:
                yield Static(_KILL_LEGEND, id="kill-legend")
            with Horizontal(id="detail-actions"):
                if self._detail is not None:
                    yield Button("SIGTERM (k)", variant="error", id="btn-kill")
                    yield Button("sudo kill (K)", variant="warning", id="btn-sudo-kill")
                yield Button("Close (Esc)", variant="primary", id="btn-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-kill":
            self.action_kill()
        elif event.button.id == "btn-sudo-kill":
            self.action_kill_sudo()
        elif event.button.id == "btn-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_kill(self) -> None:
        self._confirm_then_kill(mode="term")

    def action_kill_sudo(self) -> None:
        self._confirm_then_kill(mode="sudo")

    def _confirm_then_kill(self, *, mode: KillMode) -> None:
        if self._detail is None:
            self.notify("Kill only available for a single PID", severity="warning")
            return
        detail = self._detail

        def _after_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            if mode == "sudo":
                self._prompt_sudo_and_kill(detail)
            else:
                self._run_kill_thread(detail, password=None)

        self.app.push_screen(
            ConfirmKillScreen(detail.pid, detail.name, mode=mode),
            _after_confirm,
        )

    def _prompt_sudo_and_kill(self, detail: ProcessDetail) -> None:
        def _after_password(password: str | None) -> None:
            if password is None:
                return
            # Password already verified in the modal (sudo -v cache warm).
            self._run_kill_thread(
                detail, password=password, already_authenticated=True
            )

        self.app.push_screen(
            SudoPasswordScreen(detail.pid, detail.name),
            _after_password,
        )

    def _run_kill_thread(
        self,
        detail: ProcessDetail,
        *,
        password: str | None,
        already_authenticated: bool = False,
    ) -> None:
        app = self.app
        pid = detail.pid
        name = detail.name
        use_sudo = password is not None
        self.dismiss(None)
        if use_sudo:
            app.notify(f"sudo kill {pid} ({name})…")
        else:
            app.notify(f"SIGTERM {pid} ({name})…")

        def _notify(message: str, *, severity: str = "information") -> None:
            app.notify(message, severity=severity, markup=False)  # type: ignore[arg-type]

        def _do_kill() -> None:
            try:
                if password is not None:
                    result = HostCollector.kill_process_sudo(
                        pid,
                        password,
                        grace_seconds=2.0,
                        already_authenticated=already_authenticated,
                    )
                else:
                    result = HostCollector.kill_process(pid, grace_seconds=2.0)
                how = _KILL_RESULT_MSG.get(result, result)
                prefix = "sudo " if use_sudo else ""
                app.call_from_thread(
                    _notify, f"PID {pid} ({name}): {prefix}{how}"
                )
            except Exception as exc:  # noqa: BLE001
                app.call_from_thread(
                    _notify,
                    f"Kill failed: {exc}",
                    severity="error",
                )

        threading.Thread(target=_do_kill, daemon=True).start()
