from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never

from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoResult,
    ReadFileCommand,
    ReadFileResult,
    RequestId,
    WriteFileResult,
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

    # When
    result = dispatcher.execute(
        ReadFileCommand(
            request_id=RequestId("request-a"),
            thread_id="thread-a",
            path="notes.txt",
            start_line=2,
            max_lines=20,
        )
    )

    # Then
    match result:
        case ReadFileResult(output=output):
            assert output.content == "second"
        case ProjectInfoResult() | ListFilesResult() | WriteFileResult() | OperationErrorResult():
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
        case ProjectInfoResult() | ListFilesResult() | ReadFileResult() | WriteFileResult():
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)
