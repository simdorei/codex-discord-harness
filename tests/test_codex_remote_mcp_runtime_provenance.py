from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import codex_remote_mcp_dispatch as dispatch_module
from codex_remote_mcp_computer import ComputerController
from codex_remote_mcp_dispatch import LocalProjectDispatcher
from codex_remote_mcp_files import ProjectFileAccess
from codex_remote_mcp_terminal_engine import TerminalExecutionEngine
from codex_remote_mcp_terminal_windows import TerminalWindowManager
from simdorei_mcp_common.messages import (
    BridgeResult,
    ProjectOperationCommand,
    ProjectOperationResult,
    ProjectSessionCommand,
    ProjectSessionResult,
    RequestId,
    RuntimeCapabilityCommand,
    RuntimeCapabilityResult,
)
from simdorei_mcp_common.operation_outputs import ProjectOperationOutput
from simdorei_mcp_common.runtime_provenance import RuntimeProvenanceEnvelope
from simdorei_mcp_common.terminal_window_interaction_protocol import (
    TerminalWindowCaptureOutput,
    TerminalWindowCaptureRequest,
    TerminalWindowRect,
)
from simdorei_mcp_common.terminal_window_protocol import TerminalWindowEntry

_SESSION_BINDING = "a" * 64
_CYCLE_BINDING = "b" * 64


class RecordingRuntimeProvenance:
    def __init__(self) -> None:
        self.bound: list[ProjectSessionCommand] = []
        self.capabilities: list[RuntimeCapabilityCommand] = []
        self.terminals: list[tuple[ProjectOperationCommand, ProjectOperationOutput]] = []
        self.invalidations: list[str] = []
        self.raise_terminal: bool = False

    def bind_route(self, command: ProjectSessionCommand) -> None:
        self.bound.append(command)

    def capability(
        self, command: RuntimeCapabilityCommand
    ) -> RuntimeCapabilityResult:
        self.capabilities.append(command)
        return RuntimeCapabilityResult(
            request_id=command.request_id,
            status="accepted",
            cycle_binding_sha256=_CYCLE_BINDING,
        )

    def terminal(
        self,
        command: ProjectOperationCommand,
        output: ProjectOperationOutput,
    ) -> None:
        self.terminals.append((command, output))
        if self.raise_terminal:
            raise RuntimeError("collector failed after terminal success")

    def invalidate(self, failure_code: str) -> None:
        self.invalidations.append(failure_code)


def test_dispatch_connects_session_capability_and_terminal_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = RecordingRuntimeProvenance()
    dispatcher = _dispatcher(tmp_path, observer)
    _patch_capture(monkeypatch)
    session = dispatcher.execute(_session_command())
    capability = dispatcher.execute(_capability_command())
    terminal = dispatcher.execute(_capture_command())
    duplicate = dispatcher.execute(_capture_command())

    assert isinstance(session, ProjectSessionResult)
    assert isinstance(capability, RuntimeCapabilityResult)
    assert capability.cycle_binding_sha256 == _CYCLE_BINDING
    assert isinstance(terminal, ProjectOperationResult)
    assert duplicate == terminal
    assert len(observer.bound) == 1
    assert len(observer.capabilities) == 1
    assert len(observer.terminals) == 1


def test_terminal_observer_failure_does_not_hide_a_successful_side_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = RecordingRuntimeProvenance()
    dispatcher = _dispatcher(tmp_path, observer)
    _patch_capture(monkeypatch)
    _ = dispatcher.execute(_session_command())
    observer.raise_terminal = True

    result = dispatcher.execute(_capture_command())

    assert isinstance(result, ProjectOperationResult)
    assert isinstance(result.output, TerminalWindowCaptureOutput)
    assert observer.invalidations == ["terminal_runtime_observer_failed"]


def _dispatcher(
    root: Path,
    observer: RecordingRuntimeProvenance,
) -> LocalProjectDispatcher:
    dispatcher = LocalProjectDispatcher(runtime_provenance=observer)
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    return dispatcher


def _session_command() -> ProjectSessionCommand:
    return ProjectSessionCommand(
        request_id=RequestId("runtime-session"),
        thread_id="thread-a",
        computer_session_id="computer-session-runtime",
        runtime_provenance=RuntimeProvenanceEnvelope(
            session_binding_sha256=_SESSION_BINDING,
        ),
    )


def _capability_command() -> RuntimeCapabilityCommand:
    return RuntimeCapabilityCommand(
        request_id=RequestId("runtime-capability"),
        thread_id="thread-a",
        computer_session_id="computer-session-runtime",
        runtime_provenance=RuntimeProvenanceEnvelope(
            session_binding_sha256=_SESSION_BINDING,
        ),
        inventory_sha256="c" * 64,
    )


def _capture_command() -> ProjectOperationCommand:
    return ProjectOperationCommand(
        request_id=RequestId("runtime-terminal-capture"),
        thread_id="thread-a",
        computer_session_id="computer-session-runtime",
        runtime_provenance=RuntimeProvenanceEnvelope(
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=_CYCLE_BINDING,
        ),
        operation=TerminalWindowCaptureRequest(
            terminal_window_id="termwin_" + "d" * 16,
        ),
    )


def _patch_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(
        command: object,
        access: ProjectFileAccess,
        computer: ComputerController | None,
        *,
        terminal: TerminalExecutionEngine | None = None,
        terminal_windows: TerminalWindowManager | None = None,
    ) -> BridgeResult:
        _ = access, computer, terminal, terminal_windows
        assert isinstance(command, ProjectOperationCommand)
        return ProjectOperationResult(
            request_id=command.request_id,
            output=TerminalWindowCaptureOutput(
                window=TerminalWindowEntry(
                    terminal_window_id="termwin_" + "d" * 16,
                    window_id=42,
                    process_id=84,
                    shell="powershell",
                    cwd="C:/project",
                    title="Runtime QA",
                ),
                observation_id="twobs_" + "e" * 16,
                identity_digest="f" * 64,
                rect=TerminalWindowRect(left=0, top=0, width=640, height=480),
                data_base64="aGVsbG8gd29ybGQ=",
                captured_at=datetime.now(UTC),
            ),
        )

    monkeypatch.setattr(dispatch_module, "execute_bound_project_command", execute)
