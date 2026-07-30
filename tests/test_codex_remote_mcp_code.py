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
    ReadFileResult,
    RequestId,
    WriteFileResult,
)
from simdorei_mcp_common.operation_outputs import (
    CodeSearchOutput,
    ProjectRulesOutput,
)
from simdorei_mcp_common.operation_requests import (
    CodeSearchRequest,
    ProjectRulesRequest,
)


def test_project_rules_reads_local_rule_files(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    (root / "AGENTS.md").write_text("Use UTF-8.", encoding="utf-8")

    # When
    result = dispatcher.execute(
        _command("rules", ProjectRulesRequest())
    )

    # Then
    match result:
        case ProjectOperationResult(output=ProjectRulesOutput(rules=rules)):
            assert rules[0].path == "AGENTS.md"
            assert rules[0].content == "Use UTF-8."
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | ReadFileResult()
            | WriteFileResult()
            | OperationErrorResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def test_code_search_finds_source_and_skips_sensitive_files(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    source = root / "src"
    source.mkdir()
    (source / "app.py").write_text("needle = 1\n", encoding="utf-8")
    (root / ".env").write_text("needle=secret\n", encoding="utf-8")

    # When
    result = dispatcher.execute(
        _command(
            "search",
            CodeSearchRequest(query="needle"),
        )
    )

    # Then
    match result:
        case ProjectOperationResult(output=CodeSearchOutput(matches=matches)):
            assert tuple(match.path for match in matches) == ("src/app.py",)
            assert matches[0].line == 1
        case (
            ProjectInfoResult()
            | ListFilesResult()
            | ReadFileResult()
            | WriteFileResult()
            | OperationErrorResult()
        ):
            raise AssertionError(f"unexpected result: {result.type}")
        case unreachable:
            assert_never(unreachable)


def _bound_project(tmp_path: Path) -> tuple[Path, LocalProjectDispatcher]:
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    return root, dispatcher


def _command(
    suffix: str,
    operation: ProjectRulesRequest | CodeSearchRequest,
) -> ProjectOperationCommand:
    return ProjectOperationCommand(
        request_id=RequestId(f"request-{suffix}"),
        thread_id="thread-a",
        operation=operation,
    )
