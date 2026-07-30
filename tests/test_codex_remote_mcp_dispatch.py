from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never

from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoResult,
    ProjectOperationCommand,
    ProjectOperationResult,
    ReadFileCommand,
    ReadFileResult,
    RequestId,
    WriteFileCommand,
    WriteFileResult,
)
from simdorei_mcp_common.operation_outputs import FileCreateOutput
from simdorei_mcp_common.operation_requests import FileCreateRequest
from tests.remote_mcp_dispatch_support import (
    TEST_PROJECT_SESSION_ID,
    activate_test_session,
)


def test_dispatch_reads_from_bound_project(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("first\nsecond", encoding="utf-8")
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activate_test_session(dispatcher)

    # When
    result = dispatcher.execute(
        ReadFileCommand(
            request_id=RequestId("request-a"),
            thread_id="thread-a",
            computer_session_id=TEST_PROJECT_SESSION_ID,
            path="notes.txt",
            start_line=2,
            max_lines=20,
        )
    )

    # Then
    match result:
        case ReadFileResult(output=output):
            assert output.content == "second"
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | WriteFileResult()
            | ProjectOperationResult()
            | OperationErrorResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)

def test_dispatch_reports_expired_binding(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) - timedelta(seconds=1),
    )

    # When
    result = dispatcher.execute(
        ReadFileCommand(
            request_id=RequestId("request-a"),
            thread_id="thread-a",
            path="notes.txt",
            start_line=1,
            max_lines=20,
        )
    )

    # Then
    match result:
        case OperationErrorResult(error_code=error_code):
            assert error_code == "binding_expired"
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | ReadFileResult()
            | WriteFileResult()
            | ProjectOperationResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_dispatch_creates_project_file_operation(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activate_test_session(dispatcher)

    # When
    result = dispatcher.execute(
        ProjectOperationCommand(
            request_id=RequestId("request-create"),
            thread_id="thread-a",
            computer_session_id=TEST_PROJECT_SESSION_ID,
            operation=FileCreateRequest(
                path="notes/new.txt",
                content="created",
            ),
        )
    )

    # Then
    match result:
        case ProjectOperationResult(output=FileCreateOutput(path=path)):
            assert path == "notes/new.txt"
            assert (root / path).read_text(encoding="utf-8") == "created"
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | ReadFileResult()
            | WriteFileResult()
            | ProjectOperationResult()
            | OperationErrorResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_legacy_write_file_creates_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activate_test_session(dispatcher)

    result = dispatcher.execute(
        WriteFileCommand(
            request_id=RequestId("request-write"),
            thread_id="thread-a",
            computer_session_id=TEST_PROJECT_SESSION_ID,
            path="legacy.txt",
            content="written",
            expected_sha256=None,
        )
    )

    assert isinstance(result, WriteFileResult)
    records = tuple((root / ".codex-remote-mcp/checkpoints").glob("cp_*.json"))
    assert len(records) == 1
