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


@pytest.mark.parametrize(
    "path",
    (".env.local", "private.pem", ".npmrc", "service-token.txt", ".ssh/config"),
)
def test_sensitive_path_variants_are_never_exposed(
    tmp_path: Path,
    path: str,
) -> None:
    root = tmp_path / "project"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret", encoding="utf-8")

    with pytest.raises(UnsafeProjectPathError):
        ProjectFileAccess(root).read_file(path, start_line=1, max_lines=100)


def test_read_file_marks_redacted_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "settings.py").write_text(
        'api_key = "AbCdEfGh12345678"\n',
        encoding="utf-8",
    )

    output = ProjectFileAccess(root).read_file(
        "settings.py",
        start_line=1,
        max_lines=100,
    )

    assert output.content == 'api_key = "[REDACTED]"'
    assert output.redacted is True
