from __future__ import annotations

from codex_remote_mcp_files import ProjectFileError
from codex_remote_mcp_terminal_engine import TerminalExecutionEngine
from codex_remote_mcp_terminal_windows import TerminalWindowManager
from simdorei_mcp_common.terminal_protocol import TerminalExecRequest
from simdorei_mcp_common.terminal_window_protocol import (
    TerminalOperationOutput,
    TerminalOperationRequest,
    TerminalWindowCloseRequest,
    TerminalWindowListRequest,
    TerminalWindowOpenRequest,
)


class TerminalCapabilityError(ProjectFileError):
    """Raised when a selected session lacks a terminal runtime."""


def execute_terminal_operation(
    operation: TerminalOperationRequest,
    *,
    terminal: TerminalExecutionEngine | None,
    terminal_windows: TerminalWindowManager | None,
) -> TerminalOperationOutput:
    match operation:
        case TerminalExecRequest():
            if terminal is None:
                raise TerminalCapabilityError(
                    "terminal",
                    "terminal execution is unavailable for this project session",
                )
            return terminal.execute(operation)
        case TerminalWindowOpenRequest():
            if terminal_windows is None:
                raise TerminalCapabilityError(
                    "terminal",
                    "terminal windows are unavailable for this project session",
                )
            return terminal_windows.open(operation)
        case TerminalWindowListRequest():
            if terminal_windows is None:
                raise TerminalCapabilityError(
                    "terminal",
                    "terminal windows are unavailable for this project session",
                )
            return terminal_windows.list()
        case TerminalWindowCloseRequest():
            if terminal_windows is None:
                raise TerminalCapabilityError(
                    "terminal",
                    "terminal windows are unavailable for this project session",
                )
            return terminal_windows.close(operation)


__all__ = ["TerminalCapabilityError", "execute_terminal_operation"]
