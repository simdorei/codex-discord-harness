from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Final, Protocol

from codex_remote_mcp_files import (
    MAX_FILE_BYTES,
    ProjectFileAccess,
    ProjectFileError,
)
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.operation_outputs import (
    DiffFile,
    RepoDiffOutput,
)

MAX_GIT_OUTPUT: Final = 200_000


class GitRunner(Protocol):
    def __call__(
        self,
        root: Path,
        arguments: tuple[str, ...],
        *,
        timeout_seconds: int = 120,
    ) -> subprocess.CompletedProcess[str]: ...


def build_repo_diff(root: Path, run_git: GitRunner) -> RepoDiffOutput:
    """Return tracked and untracked changes as one bounded report."""
    tracked_paths = _visible_tracked_paths(root, run_git)
    numeric = (
        run_git(root, ("diff", "--numstat", "HEAD", "--", *tracked_paths)).stdout
        if tracked_paths
        else ""
    )
    tracked_files = tuple(_parse_numstat(numeric))
    tracked_patch = (
        run_git(
            root,
            ("diff", "--no-ext-diff", "HEAD", "--", *tracked_paths),
        ).stdout
        if tracked_paths
        else ""
    )
    untracked_files, untracked_patch = _untracked_diff(root, run_git)
    files = (*tracked_files, *untracked_files)
    patch = f"{tracked_patch}{untracked_patch}"
    truncated = len(patch) > MAX_GIT_OUTPUT
    total_added = sum(item.added for item in files)
    total_removed = sum(item.removed for item in files)
    return RepoDiffOutput(
        files=tuple(files),
        summary=f"{len(files)} file(s) changed, +{total_added}/-{total_removed}",
        patch=redact(patch[:MAX_GIT_OUTPUT]),
        truncated=truncated,
    )


def _untracked_diff(
    root: Path,
    run_git: GitRunner,
) -> tuple[tuple[DiffFile, ...], str]:
    raw_paths = run_git(
        root,
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ).stdout
    access = ProjectFileAccess(root)
    files: list[DiffFile] = []
    patches: list[str] = []
    for path in (value for value in raw_paths.split("\0") if value):
        try:
            target = access.resolve_path(path)
        except ProjectFileError:
            continue
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
            files.append(DiffFile(path=path, added=0, removed=0))
            patches.append(f"Binary untracked file: {path}\n")
            continue
        try:
            lines = raw.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            files.append(DiffFile(path=path, added=0, removed=0))
            patches.append(f"Binary untracked file: {path}\n")
            continue
        files.append(DiffFile(path=path, added=len(lines), removed=0))
        patches.append(
            "".join(
                difflib.unified_diff(
                    (),
                    lines,
                    fromfile="/dev/null",
                    tofile=f"b/{path}",
                )
            )
        )
    return tuple(files), "".join(patches)


def _parse_numstat(raw: str) -> tuple[DiffFile, ...]:
    files: list[DiffFile] = []
    for line in raw.splitlines():
        parts = line.split("\t", maxsplit=2)
        if len(parts) != 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        removed = int(parts[1]) if parts[1].isdigit() else 0
        files.append(DiffFile(path=parts[2], added=added, removed=removed))
    return tuple(files)


def _visible_tracked_paths(
    root: Path,
    run_git: GitRunner,
) -> tuple[str, ...]:
    raw = run_git(
        root,
        ("diff", "--name-only", "-z", "HEAD", "--"),
    ).stdout
    access = ProjectFileAccess(root)
    paths: list[str] = []
    for path in (value for value in raw.split("\0") if value):
        try:
            _ = access.resolve_path(path, require_file=False)
        except ProjectFileError:
            continue
        paths.append(path)
    return tuple(paths)
