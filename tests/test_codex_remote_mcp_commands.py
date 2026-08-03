# pyright: reportUnnecessaryComparison=false
from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never
from uuid import uuid4

import pytest
from pydantic import ValidationError

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
    CommandListOutput,
    CommandRunOutput,
)
from simdorei_mcp_common.operation_requests import (
    CommandListRequest,
    CommandRunRequest,
)
from tests.remote_mcp_dispatch_support import (
    TEST_PROJECT_SESSION_ID,
    activate_test_session,
)


def test_command_list_discovers_package_script(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    _write_package(root)

    # When
    result = dispatcher.execute(_command("list", CommandListRequest()))

    # Then
    match result:
        case ProjectOperationResult(output=CommandListOutput(commands=commands)):
            assert commands[0].command_id == "npm:test"
            assert commands[0].risk_tier == "verify"
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


def test_command_run_executes_discovered_verification_script() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    qa_root = repo_root / f".remote-command-qa-{uuid4().hex}"
    qa_root.mkdir()
    try:
        # Given
        root, dispatcher = _bound_project(qa_root)
        _write_package(root)

        # When
        result = dispatcher.execute(
            _command(
                "run",
                CommandRunRequest(command_id="npm:test"),
            )
        )

        # Then
        match result:
            case ProjectOperationResult(
                output=CommandRunOutput(exit_code=code, stdout=stdout)
            ):
                assert code == 0
                assert "verified" in stdout
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
    finally:
        shutil.rmtree(qa_root)


def test_command_run_rejects_caller_supplied_arguments() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = CommandRunRequest.model_validate(
            {
                "command_id": "npm:test",
                "args": ("--import=data:text/javascript,console.log('unsafe')",),
            }
        )


def test_command_run_rejects_manifest_shell_body(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    outside = tmp_path / "escaped.txt"
    _ = (root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": (
                        "node -e \"require('fs').writeFileSync("
                        f"'{outside.as_posix()}','escaped')\""
                    )
                }
            }
        ),
        encoding="utf-8",
    )

    # When
    result = dispatcher.execute(
        _command("unsafe", CommandRunRequest(command_id="npm:test"))
    )

    # Then
    assert isinstance(result, OperationErrorResult)
    assert "not remotely executable" in result.message
    assert not outside.exists()


def _bound_project(tmp_path: Path) -> tuple[Path, LocalProjectDispatcher]:
    root = tmp_path / "project"
    root.mkdir()
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activate_test_session(dispatcher)
    return root, dispatcher


def _write_package(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    _ = (tests / "verified.test.mjs").write_text(
        "import test from 'node:test';\n"
        + "test('verified', () => console.log('verified'));\n",
        encoding="utf-8",
    )
    _ = (root / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": (
                        "node --test --test-isolation=none "
                        "tests/verified.test.mjs"
                    ),
                }
            }
        ),
        encoding="utf-8",
    )


def _command(
    suffix: str,
    operation: CommandListRequest | CommandRunRequest,
) -> ProjectOperationCommand:
    return ProjectOperationCommand(
        request_id=RequestId(f"request-{suffix}"),
        thread_id="thread-a",
        computer_session_id=TEST_PROJECT_SESSION_ID,
        operation=operation,
    )
