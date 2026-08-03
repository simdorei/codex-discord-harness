from __future__ import annotations

from codex_remote_mcp_files import ProjectFileError
from codex_remote_mcp_terminal_engine import TerminalExecutionEngine
from codex_remote_mcp_terminal_windows import TerminalWindowManager
from simdorei_mcp_common.terminal_protocol import TerminalExecRequest
from simdorei_mcp_common.terminal_window_interaction_protocol import (
    TerminalWindowActivateRequest,
    TerminalWindowCaptureRequest,
    TerminalWindowInteractionOutput,
    TerminalWindowInteractionRequest,
    TerminalWindowInterruptRequest,
    TerminalWindowKeysRequest,
    TerminalWindowTypeRequest,
)
from simdorei_mcp_common.terminal_window_protocol import (
    TerminalOperationOutput,
    TerminalOperationRequest,
    TerminalWindowCloseRequest,
    TerminalWindowListRequest,
    TerminalWindowOpenRequest,
)

TerminalCapabilityRequest = TerminalOperationRequest | TerminalWindowInteractionRequest
TerminalCapabilityOutput = TerminalOperationOutput | TerminalWindowInteractionOutput


class TerminalCapabilityError(ProjectFileError):
    """Raised when a selected session lacks a terminal runtime."""


def execute_terminal_operation(
    operation: TerminalCapabilityRequest,
    *,
    terminal: TerminalExecutionEngine | None,
    terminal_windows: TerminalWindowManager | None,
) -> TerminalCapabilityOutput:
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
        case TerminalWindowCaptureRequest():
            return _require_windows(terminal_windows).capture(operation)
        case TerminalWindowActivateRequest():
            return _require_windows(terminal_windows).activate(operation)
        case TerminalWindowTypeRequest():
            return _require_windows(terminal_windows).type_text(operation)
        case TerminalWindowKeysRequest():
            return _require_windows(terminal_windows).press_keys(operation)
        case TerminalWindowInterruptRequest():
            return _require_windows(terminal_windows).interrupt(operation)


def _require_windows(
    terminal_windows: TerminalWindowManager | None,
) -> TerminalWindowManager:
    if terminal_windows is None:
        raise TerminalCapabilityError(
            "terminal",
            "terminal windows are unavailable for this project session",
        )
    return terminal_windows


__all__ = ["TerminalCapabilityError", "execute_terminal_operation"]
