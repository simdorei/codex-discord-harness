from __future__ import annotations

# pyright: reportAny=false

import ctypes
import ctypes.wintypes as wt
import time
from typing import Final

from codex_remote_mcp_terminal_engine import TerminalExecutionError
from codex_remote_mcp_terminal_window_types import OwnedTerminalWindow
from codex_remote_mcp_windows_native import KERNEL32, USER32, Rect
from simdorei_mcp_common.terminal_window_interaction_protocol import TerminalWindowRect

_SW_RESTORE: Final = 9


def require_terminal_window_rect(
    window: OwnedTerminalWindow,
) -> TerminalWindowRect:
    if window.process.poll() is not None or not USER32.IsWindow(window.entry.window_id):
        raise TerminalExecutionError("terminal window is no longer available")
    owner = wt.DWORD()
    thread_id = int(
        USER32.GetWindowThreadProcessId(window.entry.window_id, ctypes.byref(owner))
    )
    if (
        thread_id <= 0
        or window.window_process_id is None
        or int(owner.value) != window.window_process_id
    ):
        raise TerminalExecutionError("terminal window identity changed")
    rect = Rect()
    if not USER32.GetWindowRect(window.entry.window_id, ctypes.byref(rect)):
        raise TerminalExecutionError("Windows could not read terminal window bounds")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise TerminalExecutionError("terminal window has invalid bounds")
    return TerminalWindowRect(
        left=int(rect.left),
        top=int(rect.top),
        width=width,
        height=height,
    )


def activate_terminal_window(window: OwnedTerminalWindow) -> bool:
    _ = require_terminal_window_rect(window)
    window_id = window.entry.window_id
    already_active = int(USER32.GetForegroundWindow()) == window_id
    _ = USER32.ShowWindow(window_id, _SW_RESTORE)
    if not already_active:
        _set_foreground(window_id)
        time.sleep(0.12)
    if int(USER32.GetForegroundWindow()) != window_id:
        raise TerminalExecutionError("Windows did not activate the terminal window")
    return not already_active


def require_active_terminal_window(window: OwnedTerminalWindow) -> None:
    _ = require_terminal_window_rect(window)
    if int(USER32.GetForegroundWindow()) != window.entry.window_id:
        raise TerminalExecutionError("active window changed during terminal input")


def _set_foreground(window_id: int) -> None:
    if USER32.SetForegroundWindow(window_id):
        return
    foreground = int(USER32.GetForegroundWindow())
    foreground_thread = int(USER32.GetWindowThreadProcessId(foreground, None))
    current_thread = int(KERNEL32.GetCurrentThreadId())
    attached = bool(
        foreground_thread
        and foreground_thread != current_thread
        and USER32.AttachThreadInput(current_thread, foreground_thread, True)
    )
    try:
        _ = USER32.BringWindowToTop(window_id)
        _ = USER32.SetForegroundWindow(window_id)
    finally:
        if attached:
            _ = USER32.AttachThreadInput(current_thread, foreground_thread, False)


__all__ = [
    "activate_terminal_window",
    "require_active_terminal_window",
    "require_terminal_window_rect",
]
