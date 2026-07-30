from __future__ import annotations

import base64
import difflib
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from codex_remote_mcp_files import (
    FileConflictError,
    ProjectFileAccess,
    ProjectFileSizeError,
)
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.operation_outputs import CheckpointEntry

CHECKPOINT_DIRECTORY: Final = ".codex-remote-mcp/checkpoints"
MAX_CHECKPOINT_BYTES: Final = 10_485_760


class StoredSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    before_exists: bool
    before_base64: str
    after_exists: bool
    after_base64: str


class StoredCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_id: str = Field(pattern=r"^cp_[a-f0-9]{16}$")
    created_at: str
    reason: str
    snapshots: tuple[StoredSnapshot, ...]


@dataclass(frozen=True, slots=True)
class CheckpointTarget:
    path: str
    absolute_path: Path


@dataclass(frozen=True, slots=True)
class BeforeSnapshot:
    target: CheckpointTarget
    existed: bool
    content: bytes


@dataclass(frozen=True, slots=True)
class CheckpointDraft:
    root: Path
    checkpoint_id: str
    created_at: str
    reason: str
    snapshots: tuple[BeforeSnapshot, ...]


def begin_checkpoint(
    root: Path,
    reason: str,
    targets: tuple[CheckpointTarget, ...],
) -> CheckpointDraft:
    """Capture pre-mutation bytes for a bounded set of validated paths."""
    snapshots: list[BeforeSnapshot] = []
    total_bytes = 0
    for target in targets:
        existed = target.absolute_path.is_file()
        content = target.absolute_path.read_bytes() if existed else b""
        total_bytes += len(content)
        if total_bytes > MAX_CHECKPOINT_BYTES:
            raise ProjectFileSizeError(
                target.path,
                f"checkpoint exceeds {MAX_CHECKPOINT_BYTES} bytes",
            )
        snapshots.append(
            BeforeSnapshot(
                target=target,
                existed=existed,
                content=content,
            )
        )
    timestamp = datetime.now(UTC)
    seed = "\0".join(target.path for target in targets)
    digest = hashlib.sha256(
        f"{timestamp.isoformat()}\0{reason}\0{seed}".encode()
    ).hexdigest()[:16]
    return CheckpointDraft(
        root=root,
        checkpoint_id=f"cp_{digest}",
        created_at=timestamp.isoformat(),
        reason=reason,
        snapshots=tuple(snapshots),
    )


def finish_checkpoint(draft: CheckpointDraft) -> str:
    """Persist the before/after record after a successful mutation."""
    stored: list[StoredSnapshot] = []
    total_bytes = 0
    for snapshot in draft.snapshots:
        after_exists = snapshot.target.absolute_path.is_file()
        after = snapshot.target.absolute_path.read_bytes() if after_exists else b""
        total_bytes += len(snapshot.content) + len(after)
        if total_bytes > MAX_CHECKPOINT_BYTES * 2:
            raise ProjectFileSizeError(
                snapshot.target.path,
                "checkpoint before/after data is too large",
            )
        stored.append(
            StoredSnapshot(
                path=snapshot.target.path,
                before_exists=snapshot.existed,
                before_base64=base64.b64encode(snapshot.content).decode("ascii"),
                after_exists=after_exists,
                after_base64=base64.b64encode(after).decode("ascii"),
            )
        )
    record = StoredCheckpoint(
        checkpoint_id=draft.checkpoint_id,
        created_at=draft.created_at,
        reason=draft.reason,
        snapshots=tuple(stored),
    )
    directory = draft.root / CHECKPOINT_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_bytes_write(
        directory / f"{draft.checkpoint_id}.json",
        record.model_dump_json().encode("utf-8"),
    )
    return draft.checkpoint_id


def list_checkpoints(root: Path) -> tuple[CheckpointEntry, ...]:
    """Return newest checkpoint metadata without exposing stored bytes."""
    directory = root / CHECKPOINT_DIRECTORY
    if not directory.is_dir():
        return ()
    entries = [
        _entry(_load_record(path))
        for path in directory.glob("cp_*.json")
        if path.is_file()
    ]
    return tuple(sorted(entries, key=lambda entry: entry.created_at, reverse=True))


def show_checkpoint(root: Path, checkpoint_id: str) -> tuple[CheckpointEntry, str]:
    """Return checkpoint metadata and a bounded unified diff."""
    record = _load_record(_record_path(root, checkpoint_id))
    fragments = tuple(_snapshot_diff(snapshot) for snapshot in record.snapshots)
    patch = "\n".join(fragment for fragment in fragments if fragment)
    return _entry(record), redact(patch[:MAX_CHECKPOINT_BYTES])


def restore_checkpoint(root: Path, checkpoint_id: str) -> tuple[str, ...]:
    """Restore every checkpointed path to its before state."""
    record = _load_record(_record_path(root, checkpoint_id))
    access = ProjectFileAccess(root)
    targets = tuple(
        (
            snapshot,
            access.resolve_path(snapshot.path, require_file=False),
        )
        for snapshot in record.snapshots
    )
    for snapshot, target in targets:
        current_exists = target.is_file()
        expected_after = base64.b64decode(
            snapshot.after_base64,
            validate=True,
        )
        if (
            current_exists != snapshot.after_exists
            or (target.exists() and not current_exists)
            or (current_exists and target.read_bytes() != expected_after)
        ):
            raise FileConflictError(
                snapshot.path,
                "file changed after checkpoint was created",
            )
    restored: list[str] = []
    for snapshot, target in targets:
        if snapshot.before_exists:
            target.parent.mkdir(parents=True, exist_ok=True)
            content = base64.b64decode(snapshot.before_base64, validate=True)
            _atomic_bytes_write(target, content)
        else:
            target.unlink(missing_ok=True)
        restored.append(snapshot.path)
    return tuple(restored)


def rollback_checkpoint(draft: CheckpointDraft) -> None:
    """Restore captured before bytes after a mutation fails."""
    access = ProjectFileAccess(draft.root)
    for snapshot in draft.snapshots:
        target = access.resolve_path(
            snapshot.target.path,
            require_file=False,
        )
        if snapshot.existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_bytes_write(target, snapshot.content)
        else:
            target.unlink(missing_ok=True)


@contextmanager
def checkpoint_transaction(draft: CheckpointDraft) -> Iterator[None]:
    """Rollback an entire mutation if any write or persistence step fails."""
    try:
        yield
    except Exception:
        # Intentional broad recovery boundary: the checkpoint is the only
        # transaction layer spanning arbitrary filesystem and storage errors.
        rollback_checkpoint(draft)
        raise


def _record_path(root: Path, checkpoint_id: str) -> Path:
    return root / CHECKPOINT_DIRECTORY / f"{checkpoint_id}.json"


def _load_record(path: Path) -> StoredCheckpoint:
    return StoredCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


def _entry(record: StoredCheckpoint) -> CheckpointEntry:
    return CheckpointEntry(
        checkpoint_id=record.checkpoint_id,
        created_at=record.created_at,
        reason=record.reason,
    )


def _snapshot_diff(snapshot: StoredSnapshot) -> str:
    before = base64.b64decode(snapshot.before_base64, validate=True)
    after = base64.b64decode(snapshot.after_base64, validate=True)
    try:
        before_text = before.decode("utf-8").splitlines(keepends=True)
        after_text = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"Binary file changed: {snapshot.path}"
    return "".join(
        difflib.unified_diff(
            before_text,
            after_text,
            fromfile=f"a/{snapshot.path}",
            tofile=f"b/{snapshot.path}",
        )
    )


def _atomic_bytes_write(path: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
