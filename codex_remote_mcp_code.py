from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from codex_remote_mcp_files import (
    MAX_FILE_BYTES,
    ProjectFileAccess,
    UnsafeProjectPathError,
)
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.operation_outputs import (
    CodeSearchOutput,
    ProjectRulesOutput,
    RuleFile,
    SearchMatch,
)
from simdorei_mcp_common.operation_requests import CodeSearchRequest

RULE_FILES: Final = ("AGENTS.md", "CLAUDE.md", ".codex/config.toml")
SKIP_DIRECTORIES: Final = frozenset(
    {
        ".codex-remote-mcp",
        ".git",
        ".next",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "vendor",
    }
)
MAX_RULE_CHARACTERS: Final = 20_000


def read_project_rules(root: Path) -> ProjectRulesOutput:
    """Read bounded project rule files through the normal path guard."""
    access = ProjectFileAccess(root)
    rules: list[RuleFile] = []
    for candidate in RULE_FILES:
        try:
            target = access.resolve_path(candidate)
        except UnsafeProjectPathError:
            continue
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raw = raw[:MAX_FILE_BYTES]
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        rules.append(
            RuleFile(
                path=candidate,
                content=redact(content[:MAX_RULE_CHARACTERS]),
            )
        )
    return ProjectRulesOutput(rules=tuple(rules))


def search_project(root: Path, request: CodeSearchRequest) -> CodeSearchOutput:
    """Search UTF-8 project files without following links or secret paths."""
    access = ProjectFileAccess(root)
    matches: list[SearchMatch] = []
    for candidate in _walk_files(access.root):
        if len(matches) >= request.max_results:
            break
        relative = candidate.relative_to(access.root).as_posix()
        try:
            target = access.resolve_path(relative)
        except UnsafeProjectPathError:
            continue
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES or b"\0" in raw:
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            if request.query not in line:
                continue
            matches.append(
                SearchMatch(
                    path=relative,
                    line=line_number,
                    snippet=redact(line.strip()[:400]),
                )
            )
            if len(matches) >= request.max_results:
                break
    return CodeSearchOutput(matches=tuple(matches))


def _walk_files(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [
            name
            for name in names
            if name not in SKIP_DIRECTORIES
            and not (Path(directory) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(directory) / name
            if not candidate.is_symlink():
                found.append(candidate)
    return tuple(sorted(found))
