from __future__ import annotations

from pathlib import Path
from typing import assert_never

from codex_remote_mcp_checkpoints import (
    CheckpointTarget,
    begin_checkpoint,
    checkpoint_transaction,
    finish_checkpoint,
    list_checkpoints,
    restore_checkpoint,
    show_checkpoint,
)
from codex_remote_mcp_code import read_project_rules, search_project
from codex_remote_mcp_commands import list_commands, run_command
from codex_remote_mcp_files import ProjectFileAccess, ProjectFileError
from codex_remote_mcp_git import git_commit, git_push, repo_diff, repo_status
from codex_remote_mcp_images import (
    list_images,
    retrieve_image,
    save_image,
    save_image_from_url,
)
from codex_remote_mcp_patch import apply_project_patch
from simdorei_mcp_common.messages import WriteFileOutput
from simdorei_mcp_common.operation_outputs import (
    CheckpointListOutput,
    CheckpointRestoreOutput,
    CheckpointShowOutput,
    FileCreateOutput,
    ProjectOperationOutput,
    ProjectStatusOutput,
)
from simdorei_mcp_common.operation_requests import (
    CheckpointListRequest,
    CheckpointRestoreRequest,
    CheckpointShowRequest,
    CodeSearchRequest,
    CommandListRequest,
    CommandRunRequest,
    FileApplyPatchRequest,
    FileCreateRequest,
    GitCommitRequest,
    GitPushRequest,
    ListImagesRequest,
    ProjectOperation,
    ProjectRulesRequest,
    ProjectStatusRequest,
    RepoDiffRequest,
    RepoStatusRequest,
    RetrieveImageRequest,
    SaveImageFromUrlRequest,
    SaveImageRequest,
)


class ProjectCapabilityError(ProjectFileError):
    """Raised when a project capability cannot be executed."""


def execute_project_operation(
    root: Path,
    operation: ProjectOperation,
) -> ProjectOperationOutput:
    """Execute one typed capability inside a validated project root."""
    match operation:
        case ProjectRulesRequest():
            return read_project_rules(root)
        case CodeSearchRequest():
            return search_project(root, operation)
        case CommandListRequest():
            return list_commands(root)
        case CommandRunRequest():
            return run_command(root, operation)
        case FileCreateRequest():
            return _create_file(root, operation)
        case FileApplyPatchRequest():
            return apply_project_patch(root, operation)
        case RepoStatusRequest():
            return repo_status(root)
        case RepoDiffRequest():
            return repo_diff(root)
        case GitCommitRequest():
            return git_commit(root, operation)
        case GitPushRequest():
            return git_push(root, operation)
        case SaveImageRequest():
            return save_image(
                root,
                operation.path,
                operation.data_base64,
                overwrite=operation.overwrite,
            )
        case SaveImageFromUrlRequest():
            return save_image_from_url(
                root,
                operation.path,
                str(operation.url),
                overwrite=operation.overwrite,
            )
        case ListImagesRequest():
            return list_images(root)
        case RetrieveImageRequest():
            return retrieve_image(root, operation.path)
        case ProjectStatusRequest():
            return _project_status(root)
        case CheckpointListRequest():
            return CheckpointListOutput(checkpoints=list_checkpoints(root))
        case CheckpointShowRequest():
            checkpoint, patch = show_checkpoint(
                root,
                operation.checkpoint_id,
            )
            return CheckpointShowOutput(checkpoint=checkpoint, patch=patch)
        case CheckpointRestoreRequest():
            restored = restore_checkpoint(root, operation.checkpoint_id)
            return CheckpointRestoreOutput(
                checkpoint_id=operation.checkpoint_id,
                restored_files=restored,
            )
        case unreachable:
            assert_never(unreachable)


def _project_status(root: Path) -> ProjectStatusOutput:
    status = repo_status(root)
    rules = read_project_rules(root)
    commands = list_commands(root)
    return ProjectStatusOutput(
        branch=status.branch,
        dirty_files=status.dirty_files,
        staged_files=status.staged_files,
        rule_files=tuple(rule.path for rule in rules.rules),
        command_ids=tuple(command.command_id for command in commands.commands),
    )


def _create_file(
    root: Path,
    request: FileCreateRequest,
) -> FileCreateOutput:
    access = ProjectFileAccess(root)
    target = access.resolve_path(request.path, require_file=False)
    if target.exists() and not request.overwrite:
        raise ProjectCapabilityError(request.path, "file already exists")
    draft = begin_checkpoint(
        root,
        "create",
        (CheckpointTarget(path=request.path, absolute_path=target),),
    )
    expected_sha256 = None
    if target.is_file():
        expected_sha256 = access.read_file(
            request.path,
            start_line=1,
            max_lines=1,
        ).sha256
    with checkpoint_transaction(draft):
        written = access.write_file(
            request.path,
            request.content,
            expected_sha256=expected_sha256,
        )
        checkpoint_id = finish_checkpoint(draft)
    return FileCreateOutput(
        path=written.path,
        sha256=written.sha256,
        bytes_written=written.bytes_written,
        checkpoint_id=checkpoint_id,
    )


def write_file_with_checkpoint(
    root: Path,
    path: str,
    content: str,
    *,
    expected_sha256: str | None,
) -> WriteFileOutput:
    """Preserve compatibility for the legacy write tool without losing undo."""
    access = ProjectFileAccess(root)
    target = access.resolve_path(path, require_file=False)
    draft = begin_checkpoint(
        root,
        "write file",
        (CheckpointTarget(path=path, absolute_path=target),),
    )
    with checkpoint_transaction(draft):
        output = access.write_file(
            path,
            content,
            expected_sha256=expected_sha256,
        )
        finish_checkpoint(draft)
    return output
