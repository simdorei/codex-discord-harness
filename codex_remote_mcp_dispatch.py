from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import assert_never

from codex_remote_mcp_files import ProjectFileAccess, ProjectFileError
from simdorei_mcp_common.messages import (
    BridgeResult,
    GatewayCommand,
    ListFilesCommand,
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoCommand,
    ProjectInfoResult,
    ReadFileCommand,
    ReadFileResult,
    WriteFileCommand,
    WriteFileResult,
)


@dataclass(frozen=True, slots=True)
class ActiveProject:
    access: ProjectFileAccess
    expires_at: datetime


class LocalProjectDispatcher:  # MUTABLE_OK: owns synchronized project bindings.
    """Thread-safe local project registry and command executor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._projects: dict[str, ActiveProject] = {}

    def upsert(self, thread_id: str, root: Path, expires_at: datetime) -> None:
        project = ActiveProject(
            access=ProjectFileAccess(root),
            expires_at=expires_at,
        )
        with self._lock:
            self._projects[thread_id] = project

    def execute(self, command: GatewayCommand) -> BridgeResult:
        with self._lock:
            project = self._projects.get(command.thread_id)
        if project is None:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code="binding_missing",
                message="The Codex thread is not bound on this device.",
            )
        if project.expires_at <= datetime.now(UTC):
            return OperationErrorResult(
                request_id=command.request_id,
                error_code="binding_expired",
                message="The local project binding expired. Run !pro again.",
            )
        try:
            match command:
                case ProjectInfoCommand(thread_id=thread_id):
                    return ProjectInfoResult(
                        request_id=command.request_id,
                        output=project.access.project_info(thread_id),
                    )
                case ListFilesCommand(pattern=pattern, limit=limit):
                    return ListFilesResult(
                        request_id=command.request_id,
                        output=project.access.list_files(pattern, limit),
                    )
                case ReadFileCommand(path=path, start_line=start_line, max_lines=max_lines):
                    return ReadFileResult(
                        request_id=command.request_id,
                        output=project.access.read_file(
                            path,
                            start_line=start_line,
                            max_lines=max_lines,
                        ),
                    )
                case WriteFileCommand(
                    path=path,
                    content=content,
                    expected_sha256=expected_sha256,
                ):
                    return WriteFileResult(
                        request_id=command.request_id,
                        output=project.access.write_file(
                            path,
                            content,
                            expected_sha256=expected_sha256,
                        ),
                    )
                case unreachable:
                    assert_never(unreachable)
        except ProjectFileError as exc:
            return OperationErrorResult(
                request_id=command.request_id,
                error_code=_error_code(exc),
                message=str(exc),
            )


def _error_code(exc: ProjectFileError) -> str:
    return type(exc).__name__.removesuffix("Error").casefold()
