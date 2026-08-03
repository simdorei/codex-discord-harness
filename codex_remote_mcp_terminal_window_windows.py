from __future__ import annotations

# pyright: reportAny=false

import ctypes
import ctypes.wintypes as wt
import subprocess
import time
from pathlib import Path
from typing import Final, final, override

from codex_remote_mcp_redaction import redact
from codex_remote_mcp_terminal_engine import TerminalExecutionError
from codex_remote_mcp_terminal_runtime import inherited_terminal_environment
from codex_remote_mcp_terminal_window_types import (
    OwnedTerminalWindow,
    TerminalWindowBackend,
)
from codex_remote_mcp_windows_launch_types import (
    JobOwnedProcess,
    OwnedProcess,
    OwnedProcessTree,
)
from codex_remote_mcp_windows_native import USER32, EnumWindowsCallback, Rect
from codex_remote_mcp_windows_process_stop import stop_retained_process
from codex_windows_job import (
    WINDOWS_CREATE_SUSPENDED,
    create_kill_on_close_job_for_suspended_process,
)
from simdorei_mcp_common.terminal_window_protocol import (
    TerminalWindowEntry,
    TerminalWindowShell,
)

_CREATE_NEW_CONSOLE: Final = 0x00000010
_LAUNCH_TIMEOUT_SECONDS: Final = 10.0
_CLOSE_TIMEOUT_SECONDS: Final = 10.0
_TITLE_ENVIRONMENT: Final = "SIMDOREI_MCP_TERMINAL_WINDOW_TITLE"


@final
class WindowsTerminalWindowBackend(TerminalWindowBackend):
    @override
    def require_supported(self) -> None:
        return None

    @override
    def open(
        self,
        terminal_window_id: str,
        shell: TerminalWindowShell,
        cwd: Path,
        title: str,
    ) -> OwnedTerminalWindow:
        environment = _terminal_window_environment(title)
        try:
            raw_process = subprocess.Popen(
                _shell_command(shell, title),
                cwd=cwd,
                env=environment,
                close_fds=True,
                creationflags=WINDOWS_CREATE_SUSPENDED | _CREATE_NEW_CONSOLE,
            )
        except OSError as exc:
            raise TerminalExecutionError(
                f"terminal window process could not start ({type(exc).__name__})"
            ) from exc
        try:
            job = create_kill_on_close_job_for_suspended_process(raw_process.pid)
            process = JobOwnedProcess(raw_process, job)
        except OSError as exc:
            try:
                _stop_partial_process(raw_process)
            except Exception as cleanup_error:  # noqa: BLE001 - surface rollback.
                raise TerminalExecutionError(
                    "terminal window process could not be owned and its partial "
                    + "launch could not be cleaned up "
                    + f"({type(cleanup_error).__name__})"
                ) from exc
            raise TerminalExecutionError(
                f"terminal window process could not be owned ({type(exc).__name__})"
            ) from exc
        try:
            window_id = _wait_for_window(process, title)
            entry = _entry(
                terminal_window_id,
                window_id,
                process.pid,
                shell,
                cwd,
            )
        except BaseException:
            _close_owned_process(process)
            raise
        return OwnedTerminalWindow(
            entry=entry,
            process=process,
            window_process_id=_window_process_id(window_id),
        )

    @override
    def inspect(self, window: OwnedTerminalWindow) -> TerminalWindowEntry | None:
        try:
            if window.process.poll() is not None:
                return None
        except OSError as exc:
            raise TerminalExecutionError(
                "Windows could not inspect the terminal window process"
            ) from exc
        if not USER32.IsWindow(window.entry.window_id):
            return None
        if (
            window.window_process_id is not None
            and _window_process_id(window.entry.window_id)
            != window.window_process_id
        ):
            return None
        return _entry(
            window.entry.terminal_window_id,
            window.entry.window_id,
            window.process.pid,
            window.entry.shell,
            Path(window.entry.cwd),
        )

    @override
    def close(self, window: OwnedTerminalWindow) -> None:
        _close_owned_process(window.process)


def _shell_command(shell: TerminalWindowShell, title: str) -> list[str]:
    if shell == "powershell":
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NoExit",
            "-Command",
            f"$Host.UI.RawUI.WindowTitle = $env:{_TITLE_ENVIRONMENT}",
        ]
    return ["cmd.exe", "/D", "/K", f"title {title}"]


def _terminal_window_environment(title: str) -> dict[str, str]:
    return {
        **inherited_terminal_environment(),
        _TITLE_ENVIRONMENT: title,
    }


def _wait_for_window(process: JobOwnedProcess, title: str) -> int:
    deadline = time.monotonic() + _LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        matched = _find_window_by_title(title)
        if matched is not None:
            return matched
        time.sleep(0.05)
    raise TerminalExecutionError("terminal window did not open before the deadline")


def _find_window_by_title(title: str) -> int | None:
    matched: list[int] = []

    @EnumWindowsCallback
    def visit(hwnd: int, _lparam: int) -> bool:
        # Elevated cmd.exe localizes and prepends an administrator marker. The
        # complete high-entropy title still remains an unambiguous suffix.
        visible_title = _window_title(int(hwnd)).rstrip()
        if USER32.IsWindowVisible(hwnd) and visible_title.endswith(title):
            matched.append(int(hwnd))
            return False
        return True

    if not USER32.EnumWindows(visit, 0) and not matched:
        raise TerminalExecutionError("Windows could not enumerate terminal windows")
    return matched[0] if matched else None


def _entry(
    terminal_window_id: str,
    window_id: int,
    process_id: int,
    shell: TerminalWindowShell,
    cwd: Path,
) -> TerminalWindowEntry:
    if not USER32.IsWindow(window_id):
        raise TerminalExecutionError("terminal window is no longer available")
    rect = Rect()
    if not USER32.GetWindowRect(window_id, ctypes.byref(rect)):
        raise TerminalExecutionError("Windows could not inspect the terminal window")
    title = redact(_window_title(window_id))[:500]
    if not title:
        raise TerminalExecutionError("terminal window has no usable title")
    return TerminalWindowEntry(
        terminal_window_id=terminal_window_id,
        window_id=window_id,
        process_id=process_id,
        shell=shell,
        cwd=str(cwd),
        title=title,
    )


def _window_title(window_id: int) -> str:
    length = int(USER32.GetWindowTextLengthW(window_id))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    USER32.GetWindowTextW(window_id, buffer, length + 1)
    return buffer.value


def _window_process_id(window_id: int) -> int:
    owner = wt.DWORD()
    thread_id = int(USER32.GetWindowThreadProcessId(window_id, ctypes.byref(owner)))
    if thread_id <= 0 or owner.value <= 0:
        raise TerminalExecutionError("Windows could not identify the terminal window")
    return int(owner.value)


def _close_owned_process(process: OwnedProcess) -> None:
    if not isinstance(process, OwnedProcessTree):
        raise TerminalExecutionError("terminal window process tree is not owned")
    try:
        process.terminate_tree_and_close(timeout_seconds=_CLOSE_TIMEOUT_SECONDS)
    except (OSError, TimeoutError) as exc:
        raise TerminalExecutionError(
            "terminal window process tree did not close"
        ) from exc


def _stop_partial_process(process: subprocess.Popen[bytes]) -> None:
    stop_retained_process(
        process,
        terminate_timeout_seconds=1.0,
        kill_timeout_seconds=5.0,
    )


__all__ = ["WindowsTerminalWindowBackend"]
