from __future__ import annotations

from pathlib import Path

import pytest

from codex_remote_mcp_files import (
    FileConflictError,
    ProjectFileAccess,
    UnsafeProjectPathError,
)


def test_read_file_rejects_parent_traversal(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("private", encoding="utf-8")
    access = ProjectFileAccess(root)

    # When / Then
    with pytest.raises(UnsafeProjectPathError):
        access.read_file("../outside.txt", start_line=1, max_lines=100)


def test_write_file_requires_current_hash_for_existing_file(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)

    # When / Then
    with pytest.raises(FileConflictError):
        access.write_file("notes.txt", "after", expected_sha256=None)


def test_write_file_accepts_matching_hash(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=100)

    # When
    result = access.write_file(
        "notes.txt",
        "after",
        expected_sha256=current.sha256,
    )

    # Then
    assert target.read_text(encoding="utf-8") == "after"
    assert result.created is False
    assert result.sha256 != current.sha256


def test_sensitive_file_is_never_exposed(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    access = ProjectFileAccess(root)

    # When / Then
    with pytest.raises(UnsafeProjectPathError):
        access.read_file(".env", start_line=1, max_lines=100)
