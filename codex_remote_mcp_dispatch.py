# pyright: reportUnnecessaryComparison=false
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import final, overload

from codex_remote_mcp_computer import (
    ComputerController,
    is_computer_operation,
    new_computer_controller,
)
from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_dispatch_commands import execute_bound_project_command
from codex_remote_mcp_dispatch_state import ActiveProject, ProjectDispatchState
from codex_remote_mcp_files import ProjectFileError
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.messages import (
    BridgeResult,
    GatewayCommand,
    ListFilesCommand,
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoCommand,
    ProjectInfoResult,
    ProjectOperationCommand,
    ProjectOperationResult,
    ProjectSessionCommand,
    ProjectSessionResult,
    ReadFileCommand,
    ReadFileResult,
    WriteFileCommand,
    WriteFileResult,
)
from simdorei_mcp_common.operation_outputs import ComputerStopOutput
from simdorei_mcp_common.operation_requests import ComputerStopRequest


@final
class LocalProjectDispatcher:  # MUTABLE_OK: owns synchronized project bindings.
    """Thread-safe local project registry and command executor."""

    def __init__(
        self,
        *,
        computer_factory: Callable[[], ComputerController] = new_computer_controller,
    ) -> None:
        self._state = ProjectDispatchState(computer_factory)

    def upsert(self, thread_id: str, root: Path, expires_at: datetime) -> None:
        self._state.upsert(thread_id, root, expires_at)

    @overload
    def execute(
        self, command: ProjectSessionCommand
    ) -> ProjectSessionResult | OperationErrorResult: ...

    @overload
    def execute(
        self, command: ProjectInfoCommand
    ) -> ProjectInfoResult | OperationErrorResult: ...

    @overload
    def execute(
        self, command: ListFilesCommand
    ) -> ListFilesResult | OperationErrorResult: ...

    @overload
    def execute(
        self, command: ReadFileCommand
    ) -> ReadFileResult | OperationErrorResult: ...

    @overload
    def execute(
        self, command: WriteFileCommand
    ) -> WriteFileResult | OperationErrorResult: ...

    @overload
    def execute(
        self, command: ProjectOperationCommand
    ) -> ProjectOperationResult | OperationErrorResult: ...

    @overload
    def execute(self, command: GatewayCommand) -> BridgeResult: ...

    def execute(self, command: GatewayCommand) -> BridgeResult:
        project = self._state.binding(command.thread_id)
        if project is None:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code="binding_missing",
                message="The Codex thread is not bound on this device.",
            )
        if isinstance(command, ProjectOperationCommand) and isinstance(
            command.operation,
            ComputerStopRequest,
        ):
            return self._execute_computer_stop(command, project)
        with project.execution_lock:
            return self._execute_locked(command, project)

    def _execute_computer_stop(
        self,
        command: ProjectOperationCommand,
        project: ActiveProject,
    ) -> ProjectOperationResult | OperationErrorResult:
        try:
            if project.expires_at <= datetime.now(UTC):
                self._state.stop_computer_if_bound(command.thread_id, project)
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code="binding_expired",
                    message="The local project binding expired. Run !pro again.",
                )
            self._state.stop_computer_if_current(
                command.thread_id, project, command.computer_session_id
            )
        except ProjectFileError as exc:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code=_error_code(exc),
                message=redact(str(exc)),
            )
        return ProjectOperationResult(
            request_id=command.request_id,
            output=ComputerStopOutput(
                message="Computer control stopped until this project is bound again."
            ),
        )

    def _execute_locked(
        self,
        command: GatewayCommand,
        project: ActiveProject,
    ) -> BridgeResult:
        computer: ComputerController | None = None
        if project.expires_at <= datetime.now(UTC):
            self._stop_computer(command.thread_id)
            return OperationErrorResult(
                request_id=command.request_id,
                error_code="binding_expired",
                message="The local project binding expired. Run !pro again.",
            )
        if isinstance(command, ProjectSessionCommand):
            generation = command.computer_session_id
            if generation is None:
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code="computer_session_missing",
                    message="The project session command has no generation.",
                )
            try:
                self._activate_project_session(
                    command.thread_id,
                    project,
                    generation,
                )
            except ProjectFileError as exc:
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code=_error_code(exc),
                    message=redact(str(exc)),
                )
            return ProjectSessionResult(request_id=command.request_id)
        try:
            self._require_project_session(
                command.thread_id,
                project,
                command.computer_session_id,
            )
        except ProjectFileError as exc:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code=_error_code(exc),
                message=redact(str(exc)),
            )
        if isinstance(command, ProjectOperationCommand) and is_computer_operation(
            command.operation
        ):
            if self._state.is_computer_stopped(command.thread_id):
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code="computer_control_stopped",
                    message="Computer control is stopped. Run !pro again to renew it.",
                )
            try:
                computer = self._computer_for(
                    command.thread_id,
                    project,
                    command.computer_session_id,
                )
            except ProjectFileError as exc:
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code=_error_code(exc),
                    message=redact(str(exc)),
                )
            if computer is None:
                return OperationErrorResult(
                    request_id=command.request_id,
                    error_code="computer_control_stopped",
                    message="Computer control is stopped. Run !pro again to renew it.",
                )
        try:
            return execute_bound_project_command(command, project.access, computer)
        except ProjectFileError as exc:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code=_error_code(exc),
                message=redact(str(exc)),
            )

    def _computer_for(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        computer_session_id: str | None,
    ) -> ComputerController | None:
        return self._state.computer_for(
            thread_id,
            expected_project,
            computer_session_id,
        )

    def _activate_project_session(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        computer_session_id: str,
    ) -> None:
        self._state.activate(
            thread_id,
            expected_project,
            computer_session_id,
        )

    def _require_project_session(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        computer_session_id: str | None,
    ) -> None:
        self._state.require(thread_id, expected_project, computer_session_id)

    def invalidate_computer_sessions(self) -> None:
        self._state.invalidate_sessions()

    def _stop_computer(self, thread_id: str) -> None:
        self._state.stop_computer(thread_id)


def _error_code(exc: ProjectFileError) -> str:
    if isinstance(exc, ComputerControlError):
        return "computer_control"
    return type(exc).__name__.removesuffix("Error").casefold()
