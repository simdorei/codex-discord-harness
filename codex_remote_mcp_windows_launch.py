# pyright: reportAny=false
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Final

from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_windows_launch_cleanup import (
    remove_temporary_profile,
)
from codex_remote_mcp_windows_launch_cleanup import (
    retry_failed_launch_cleanup as _retry_failed_launch_cleanup,
)
from codex_remote_mcp_windows_launch_types import (
    ApplicationLaunchCleanupError,
    FailedLaunchCleanup,
    LaunchedApplication,
    OwnedProcess,
)
from codex_remote_mcp_windows_native import USER32, EnumWindowsCallback
from codex_remote_mcp_windows_process_stop import stop_retained_process
from codex_remote_mcp_windows_windows import ResolvedWindow, resolve_allowed_window

LAUNCH_TIMEOUT_SECONDS: Final = 8.0
OWNED_PROCESS_CLOSE_SECONDS: Final = 1.0
OWNED_PROCESS_KILL_SECONDS: Final = 5.0


def launch_allowed_app(app: str) -> LaunchedApplication:
    executable = _app_executable(app)
    temporary_profile = (
        tempfile.mkdtemp(prefix="simdorei-mcp-chrome-") if app == "chrome" else None
    )
    command = _launch_command(executable, app, temporary_profile)
    try:
        process = subprocess.Popen(command, close_fds=True)
    except OSError as exc:
        _cleanup_failed_launch(
            FailedLaunchCleanup(app, None, temporary_profile),
            exc,
        )
        raise ComputerControlError(f"Could not launch {app}: {exc}") from exc
    try:
        resolved = _wait_for_main_window(process)
    except ComputerControlError as exc:
        _cleanup_failed_launch(
            FailedLaunchCleanup(app, process, temporary_profile),
            exc,
        )
        raise
    return LaunchedApplication(
        window=resolved,
        process=process,
        temporary_profile=temporary_profile,
    )


def _launch_command(
    executable: str,
    app: str,
    temporary_profile: str | None,
) -> list[str]:
    if app == "notepad":
        return [executable]
    if temporary_profile is None:
        raise ComputerControlError("Chrome requires an isolated temporary profile.")
    return [
        executable,
        f"--user-data-dir={temporary_profile}",
        "--guest",
        "--no-first-run",
        "--disable-sync",
        "--disable-background-mode",
        "--new-window",
        "about:blank",
    ]


def _wait_for_main_window(process: subprocess.Popen[bytes]) -> ResolvedWindow:
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        for window_id in _visible_windows_for_process(process.pid):
            try:
                return resolve_allowed_window(window_id)
            except ComputerControlError:
                continue
        time.sleep(0.05)
    raise ComputerControlError(
        "The launched application did not open a safe window in time."
    )


def _visible_windows_for_process(process_id: int) -> tuple[int, ...]:
    windows: list[int] = []

    @EnumWindowsCallback
    def visit(hwnd: int, _lparam: int) -> bool:
        owner = wt.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if int(owner.value) == process_id and USER32.IsWindowVisible(hwnd):
            windows.append(int(hwnd))
        return True

    if not USER32.EnumWindows(visit, 0):
        raise ComputerControlError(
            "Windows could not inspect the launched application."
        )
    return tuple(windows)


def retry_failed_launch_cleanup(cleanup: FailedLaunchCleanup) -> None:
    _retry_failed_launch_cleanup(cleanup, remove_temporary_profile)


def _cleanup_failed_launch(
    cleanup: FailedLaunchCleanup,
    launch_error: BaseException,
) -> None:
    try:
        retry_failed_launch_cleanup(cleanup)
    except ApplicationLaunchCleanupError as exc:
        raise exc from launch_error


def stop_owned_process(
    window_id: int,
    process: OwnedProcess,
    process_path: str,
) -> None:
    _ = window_id, process_path
    stop_retained_process(
        process,
        terminate_timeout_seconds=OWNED_PROCESS_CLOSE_SECONDS,
        kill_timeout_seconds=OWNED_PROCESS_KILL_SECONDS,
    )


def _app_executable(app: str) -> str:
    if app == "notepad":
        windows = os.environ.get("SYSTEMROOT")
        candidate = Path(windows) / "System32/notepad.exe" if windows else None
        if candidate is not None and candidate.is_file():
            return str(candidate)
        raise ComputerControlError("Windows Notepad is not installed in System32.")
    if app != "chrome":
        raise ComputerControlError(f"Application is not allowlisted: {app}")
    candidates = tuple(
        base / "Google/Chrome/Application/chrome.exe"
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        if (value := os.environ.get(variable))
        for base in (Path(value),)
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ComputerControlError("Google Chrome is not installed in a standard location.")
