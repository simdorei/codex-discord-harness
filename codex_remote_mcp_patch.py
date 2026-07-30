# pyright: reportUnnecessaryComparison=false
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from codex_remote_mcp_checkpoint_transaction import (
    CheckpointTarget,
    CheckpointTransaction,
    checkpoint_transaction,
)
from codex_remote_mcp_checkpoints import (
    begin_checkpoint,
    finish_checkpoint,
)
from codex_remote_mcp_files import (
    FileConflictError,
    ProjectFileAccess,
)
from codex_remote_mcp_patch_parser import (
    AddOperation,
    DeleteOperation,
    PatchFormatError,
    PatchHunk,
    PatchOperation,
    UpdateOperation,
    parse_patch,
)
from simdorei_mcp_common.operation_outputs import FileApplyPatchOutput, PatchEntry
from simdorei_mcp_common.operation_requests import FileApplyPatchRequest


@dataclass(frozen=True, slots=True)
class PlannedMutation:
    entry: PatchEntry
    source: CheckpointTarget
    destination: CheckpointTarget | None
    content: str | None
    expected_sha256: str | None


def apply_project_patch(
    root: Path,
    request: FileApplyPatchRequest,
) -> FileApplyPatchOutput:
    """Apply a bounded Codex patch with SHA checks and rollback checkpoints."""
    access = ProjectFileAccess(root)
    planned = tuple(
        _plan_mutation(access, operation, request.precondition_hashes)
        for operation in parse_patch(request.patch)
    )
    checkpoint_targets = _checkpoint_targets(planned)
    draft = begin_checkpoint(root, "patch", checkpoint_targets)
    with checkpoint_transaction(draft) as transaction:
        _apply_mutations(access, planned, transaction)
        checkpoint_id = finish_checkpoint(draft)
    return FileApplyPatchOutput(
        applied=tuple(mutation.entry for mutation in planned),
        checkpoint_id=checkpoint_id,
    )


def _plan_mutation(
    access: ProjectFileAccess,
    operation: PatchOperation,
    hashes: dict[str, str],
) -> PlannedMutation:
    match operation:
        case AddOperation(path=path, content=content):
            target = access.resolve_path(path, require_file=False)
            if access.file_exists(path):
                raise FileConflictError(path, "added file already exists")
            return PlannedMutation(
                entry=PatchEntry(
                    path=path,
                    action="add",
                    added_lines=len(content.splitlines()),
                    removed_lines=0,
                ),
                source=CheckpointTarget(path=path, absolute_path=target),
                destination=None,
                content=content,
                expected_sha256=None,
            )
        case DeleteOperation(path=path):
            target = access.resolve_path(path)
            expected = _require_hash(access, path, hashes)
            return PlannedMutation(
                entry=PatchEntry(
                    path=path,
                    action="delete",
                    added_lines=0,
                    removed_lines=0,
                ),
                source=CheckpointTarget(path=path, absolute_path=target),
                destination=None,
                content=None,
                expected_sha256=expected,
            )
        case UpdateOperation(path=path, destination=destination, hunks=hunks):
            source = access.resolve_path(path)
            expected = _require_hash(access, path, hashes)
            original = access.read_bytes(path).decode("utf-8")
            content = _apply_hunks(original, hunks) if hunks else original
            destination_target = None
            action: Literal["update", "move"] = "update"
            if destination is not None:
                target = access.resolve_path(destination, require_file=False)
                if access.file_exists(destination):
                    raise FileConflictError(
                        destination, "move destination already exists"
                    )
                destination_target = CheckpointTarget(
                    path=destination,
                    absolute_path=target,
                )
                action = "move"
            added, removed = _count_delta(hunks)
            return PlannedMutation(
                entry=PatchEntry(
                    path=path,
                    action=action,
                    destination=destination,
                    added_lines=added,
                    removed_lines=removed,
                ),
                source=CheckpointTarget(path=path, absolute_path=source),
                destination=destination_target,
                content=content,
                expected_sha256=expected,
            )
        case unreachable:
            assert_never(unreachable)


def _require_hash(
    access: ProjectFileAccess,
    path: str,
    hashes: dict[str, str],
) -> str:
    expected = hashes.get(path)
    if expected is None:
        raise FileConflictError(path, "existing files require a precondition hash")
    current = hashlib.sha256(access.read_bytes(path)).hexdigest()
    if current != expected:
        raise FileConflictError(path, "file changed since it was read")
    return expected


def _apply_hunks(original: str, hunks: tuple[PatchHunk, ...]) -> str:
    lines = original.splitlines()
    trailing_newline = original.endswith("\n")
    search_from = 0
    for hunk in hunks:
        searched = tuple(
            line.text for line in hunk.lines if line.kind in {"context", "remove"}
        )
        replacement = tuple(
            line.text for line in hunk.lines if line.kind in {"context", "add"}
        )
        position = _find_sequence(lines, searched, search_from)
        lines[position : position + len(searched)] = replacement
        search_from = position + len(replacement)
    result = "\n".join(lines)
    return result + ("\n" if trailing_newline else "")


def _find_sequence(lines: list[str], wanted: tuple[str, ...], start: int) -> int:
    if not wanted:
        return len(lines)
    limit = len(lines) - len(wanted) + 1
    for index in range(start, max(start, limit)):
        if tuple(lines[index : index + len(wanted)]) == wanted:
            return index
    raise PatchFormatError("<patch>", "update context was not found")


def _count_delta(hunks: tuple[PatchHunk, ...]) -> tuple[int, int]:
    added = sum(line.kind == "add" for hunk in hunks for line in hunk.lines)
    removed = sum(line.kind == "remove" for hunk in hunks for line in hunk.lines)
    return added, removed


def _checkpoint_targets(
    mutations: tuple[PlannedMutation, ...],
) -> tuple[CheckpointTarget, ...]:
    unique: dict[str, CheckpointTarget] = {}
    for mutation in mutations:
        unique[mutation.source.path] = mutation.source
        if mutation.destination is not None:
            unique[mutation.destination.path] = mutation.destination
    return tuple(unique.values())


def _apply_mutations(
    access: ProjectFileAccess,
    mutations: tuple[PlannedMutation, ...],
    transaction: CheckpointTransaction,
) -> None:
    for mutation in mutations:
        if mutation.expected_sha256 is not None:
            current = hashlib.sha256(
                access.read_bytes(mutation.source.path)
            ).hexdigest()
            if current != mutation.expected_sha256:
                raise FileConflictError(
                    mutation.source.path,
                    "file changed since the patch was planned",
                )
        if mutation.entry.action == "delete":
            access.delete_file(
                mutation.source.path,
                expected_sha256=mutation.expected_sha256 or "",
            )
            transaction.record_delete(mutation.source.path)
            continue
        if mutation.destination is not None:
            written = access.write_file(
                mutation.destination.path,
                mutation.content or "",
                expected_sha256=None,
            )
            transaction.record_write(mutation.destination.path, written.sha256)
            access.delete_file(
                mutation.source.path,
                expected_sha256=mutation.expected_sha256 or "",
            )
            transaction.record_delete(mutation.source.path)
            continue
        written = access.write_file(
            mutation.source.path,
            mutation.content or "",
            expected_sha256=mutation.expected_sha256,
        )
        transaction.record_write(mutation.source.path, written.sha256)
