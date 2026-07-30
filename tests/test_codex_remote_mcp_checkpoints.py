from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codex_remote_mcp_checkpoints import restore_checkpoint
from codex_remote_mcp_dispatch import LocalProjectDispatcher
from codex_remote_mcp_files import FileConflictError
from simdorei_mcp_common.messages import (
    ProjectOperationCommand,
    ProjectOperationResult,
    RequestId,
)
from simdorei_mcp_common.operation_outputs import (
    CheckpointListOutput,
    CheckpointRestoreOutput,
    ProjectStatusOutput,
)
from simdorei_mcp_common.operation_requests import (
    CheckpointListRequest,
    CheckpointRestoreRequest,
    FileCreateRequest,
    ProjectStatusRequest,
)


def test_checkpoint_list_reports_file_create_checkpoint(tmp_path: Path) -> None:
    # Given
    _, dispatcher = _bound_project(tmp_path)
    created = dispatcher.execute(
        _command("create", FileCreateRequest(path="note.txt", content="new"))
    )
    assert isinstance(created, ProjectOperationResult)

    # When
    result = dispatcher.execute(_command("list", CheckpointListRequest()))

    # Then
    assert isinstance(result, ProjectOperationResult)
    assert isinstance(result.output, CheckpointListOutput)
    assert len(result.output.checkpoints) == 1


def test_checkpoint_restore_removes_created_file(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    created = dispatcher.execute(
        _command("create", FileCreateRequest(path="note.txt", content="new"))
    )
    assert isinstance(created, ProjectOperationResult)
    checkpoint_id = created.output.checkpoint_id

    # When
    result = dispatcher.execute(
        _command(
            "restore",
            CheckpointRestoreRequest(checkpoint_id=checkpoint_id),
        )
    )

    # Then
    assert isinstance(result, ProjectOperationResult)
    assert isinstance(result.output, CheckpointRestoreOutput)
    assert not (root / "note.txt").exists()


def test_project_status_combines_git_rules_and_commands(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    (root / "AGENTS.md").write_text("rules", encoding="utf-8")
    (root / "package.json").write_text(
        '{"scripts":{"test":"node --test"}}',
        encoding="utf-8",
    )
    _git(root, "init")

    # When
    result = dispatcher.execute(_command("status", ProjectStatusRequest()))

    # Then
    assert isinstance(result, ProjectOperationResult)
    assert isinstance(result.output, ProjectStatusOutput)
    assert result.output.rule_files == ("AGENTS.md",)
    assert result.output.command_ids == ("npm:test",)


def test_checkpoint_restore_rejects_later_file_change(tmp_path: Path) -> None:
    # Given
    root, dispatcher = _bound_project(tmp_path)
    created = dispatcher.execute(
        _command("create", FileCreateRequest(path="note.txt", content="new"))
    )
    assert isinstance(created, ProjectOperationResult)
    (root / "note.txt").write_text("later", encoding="utf-8")

    # When / Then
    with pytest.raises(FileConflictError, match="changed after checkpoint"):
        restore_checkpoint(root, created.output.checkpoint_id)
    assert (root / "note.txt").read_text(encoding="utf-8") == "later"


def test_file_create_rolls_back_when_checkpoint_persist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, dispatcher = _bound_project(tmp_path)
    monkeypatch.setattr(
        "codex_remote_mcp_operations.finish_checkpoint",
        lambda _draft: (_ for _ in ()).throw(OSError("disk failure")),
    )

    with pytest.raises(OSError, match="disk failure"):
        dispatcher.execute(
            _command(
                "rollback-create",
                FileCreateRequest(path="note.txt", content="new"),
            )
        )

    assert not (root / "note.txt").exists()


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
    operation: (
        CheckpointListRequest
        | CheckpointRestoreRequest
        | FileCreateRequest
        | ProjectStatusRequest
    ),
) -> ProjectOperationCommand:
    return ProjectOperationCommand.model_validate(
        {
            "request_id": RequestId(f"request-{suffix}"),
            "thread_id": "thread-a",
            "operation": operation,
        }
    )


def _git(root: Path, *arguments: str) -> None:
    import subprocess

    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
