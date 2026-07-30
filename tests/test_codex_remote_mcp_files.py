"""Cohesive file-confinement and atomicity contracts. (# noqa: SIZE_OK)"""

from __future__ import annotations

# pyright: reportDeprecated=false, reportPrivateLocalImportUsage=false
# pyright: reportUnusedCallResult=false, reportUnusedParameter=false
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

import codex_remote_mcp_windows_file_condition as windows_file_condition
import codex_remote_mcp_windows_file_guard as windows_file_guard
import codex_remote_mcp_windows_file_native as windows_file_native
import codex_remote_mcp_windows_file_staging as windows_file_staging
import codex_remote_mcp_windows_file_write as windows_file_write
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


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-handle semantics")
def test_read_retains_file_identity_during_a_swap_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("trusted", encoding="utf-8")
    replacement = root / "replacement.txt"
    replacement.write_text("untrusted", encoding="utf-8")
    access = ProjectFileAccess(root)
    real_stream = windows_file_guard.binary_stream

    @contextmanager
    def attempt_swap(handle: int, *, writable: bool) -> Iterator[BinaryIO]:
        with pytest.raises(PermissionError):
            os.replace(replacement, target)
        with real_stream(handle, writable=writable) as stream:
            yield stream

    monkeypatch.setattr(windows_file_guard, "binary_stream", attempt_swap)

    assert access.read_bytes("notes.txt") == b"trusted"
    assert target.read_text(encoding="utf-8") == "trusted"


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-root semantics")
def test_bound_project_root_cannot_be_replaced(tmp_path: Path) -> None:
    root = tmp_path / "project"
    moved = tmp_path / "moved"
    root.mkdir()
    access = ProjectFileAccess(root)

    root.rename(moved)

    with pytest.raises(UnsafeProjectPathError, match="moved or changed"):
        access.verify_root()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic replacement semantics")
def test_failed_atomic_replace_preserves_the_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_rename = windows_file_write.rename_file_by_handle

    def fail_rename(
        handle: int,
        destination: Path,
        *,
        replace_existing: bool,
    ) -> None:
        assert replace_existing is False
        if destination.suffix == ".bak":
            raise OSError("simulated atomic replacement failure")
        real_rename(handle, destination, replace_existing=replace_existing)

    monkeypatch.setattr(windows_file_write, "rename_file_by_handle", fail_rename)

    with pytest.raises(UnsafeProjectPathError, match="replacement failure"):
        _ = access.write_file(
            "notes.txt",
            "after",
            expected_sha256=current.sha256,
        )

    assert target.read_text(encoding="utf-8") == "before"
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows staged-file cleanup semantics")
def test_staging_verification_failure_removes_the_temporary_file(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"

    def reject_staged_file(_handle: int) -> windows_file_native.ByHandleFileInformation:
        raise OSError("simulated verification failure")

    with pytest.raises(OSError, match="verification failure"):
        _ = windows_file_staging.write_temporary(
            target,
            b"after",
            reject_staged_file,
        )

    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic rollback semantics")
def test_failed_publication_restores_the_moved_original_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)

    def fail_link(_: int, __: Path) -> None:
        raise OSError("simulated publication failure")

    monkeypatch.setattr(windows_file_write, "link_file_by_handle", fail_link)

    with pytest.raises(UnsafeProjectPathError, match="publication failure"):
        _ = access.write_file(
            "notes.txt",
            "after",
            expected_sha256=current.sha256,
        )

    assert target.read_text(encoding="utf-8") == "before"
    assert tuple(root.glob(".notes.txt.*.bak")) == ()
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-write-lock semantics")
def test_atomic_replace_locks_the_validated_file_against_concurrent_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_rename = windows_file_write.rename_file_by_handle
    write_was_blocked = False

    def attempt_concurrent_write(
        handle: int,
        destination: Path,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal write_was_blocked
        with pytest.raises(OSError):
            target.write_bytes(b"concurrent")
        write_was_blocked = True
        real_rename(handle, destination, replace_existing=replace_existing)

    monkeypatch.setattr(
        windows_file_write,
        "rename_file_by_handle",
        attempt_concurrent_write,
    )

    result = access.write_file(
        "notes.txt",
        "after",
        expected_sha256=current.sha256,
    )

    assert result.created is False
    assert write_was_blocked is True
    assert target.read_text(encoding="utf-8") == "after"


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic replacement semantics")
def test_atomic_replace_preserves_a_competing_native_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    competitor = root / "competitor.txt"
    competitor.write_text("concurrent", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_rename = windows_file_write.rename_file_by_handle
    replacement_was_attempted = False

    def rename_after_competitor(
        handle: int,
        destination: Path,
        *,
        replace_existing: bool,
    ) -> None:
        nonlocal replacement_was_attempted
        if not replacement_was_attempted:
            replacement_was_attempted = True
            competitor_handle = windows_file_native.open_handle(
                competitor,
                windows_file_native.DELETE_ACCESS
                | windows_file_native.FILE_READ_ATTRIBUTES,
                windows_file_native.OPEN_EXISTING,
                share_delete=True,
            )
            try:
                windows_file_native.replace_file_by_handle(competitor_handle, target)
            finally:
                windows_file_native.close_handle(competitor_handle)
        real_rename(handle, destination, replace_existing=replace_existing)

    monkeypatch.setattr(
        windows_file_write,
        "rename_file_by_handle",
        rename_after_competitor,
    )

    with pytest.raises(FileConflictError):
        _ = access.write_file(
            "notes.txt",
            "ours",
            expected_sha256=current.sha256,
        )

    assert replacement_was_attempted is True
    assert target.read_text(encoding="utf-8") == "concurrent"
    assert tuple(root.glob(".notes.txt.*.bak")) == ()
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-temp semantics")
def test_atomic_replace_blocks_native_temporary_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    attacker = root / "attacker.txt"
    attacker.write_text("do not delete", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_link = windows_file_write.link_file_by_handle
    replacement_was_blocked = False

    def replace_temporary_before_link(handle: int, destination: Path) -> None:
        nonlocal replacement_was_blocked
        temporary_paths = tuple(root.glob(".notes.txt.*.tmp"))
        assert len(temporary_paths) == 1
        attacker_handle = windows_file_native.open_handle(
            attacker,
            windows_file_native.DELETE_ACCESS
            | windows_file_native.FILE_READ_ATTRIBUTES,
            windows_file_native.OPEN_EXISTING,
            share_delete=True,
        )
        try:
            with pytest.raises(OSError):
                windows_file_native.replace_file_by_handle(
                    attacker_handle,
                    temporary_paths[0],
                )
            replacement_was_blocked = True
        finally:
            windows_file_native.close_handle(attacker_handle)
        real_link(handle, destination)

    monkeypatch.setattr(
        windows_file_write,
        "link_file_by_handle",
        replace_temporary_before_link,
    )

    _ = access.write_file(
        "notes.txt",
        "after",
        expected_sha256=current.sha256,
    )

    assert replacement_was_blocked is True
    assert attacker.read_text(encoding="utf-8") == "do not delete"
    assert target.read_text(encoding="utf-8") == "after"
    assert tuple(root.glob(".notes.txt.*.bak")) == ()
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-temp semantics")
def test_atomic_replace_rejects_in_place_temporary_content_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_retain = windows_file_write.retain_temporary

    def tamper_then_retain(
        temporary: Path,
        expected_identity: windows_file_condition.FileIdentity,
        expected_sha256: str,
        verify_regular_file: windows_file_condition.VerifyRegularFile,
    ) -> int:
        _ = temporary.write_bytes(b"attacker-content")
        return real_retain(
            temporary,
            expected_identity,
            expected_sha256,
            verify_regular_file,
        )

    monkeypatch.setattr(windows_file_write, "retain_temporary", tamper_then_retain)

    with pytest.raises(UnsafeProjectPathError, match="temporary file content changed"):
        _ = access.write_file(
            "notes.txt",
            "after",
            expected_sha256=current.sha256,
        )

    assert target.read_text(encoding="utf-8") == "before"
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS hard-link semantics")
def test_read_rejects_a_hard_link_to_sensitive_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / ".env"
    outside.write_text("TOKEN=private", encoding="utf-8")
    os.link(outside, root / "notes.txt")

    with pytest.raises(UnsafeProjectPathError, match="hard links"):
        ProjectFileAccess(root).read_bytes("notes.txt")


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-result semantics")
def test_published_file_detects_and_preserves_a_late_native_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    competitor = root / "competitor.txt"
    competitor.write_text("concurrent", encoding="utf-8")
    access = ProjectFileAccess(root)
    current = access.read_file("notes.txt", start_line=1, max_lines=10)
    real_verify = windows_file_write.verify_committed
    replacement_happened = False

    def verify_after_competing_replacement(
        destination: Path,
        expected_identity: tuple[int, int, int],
        verify_regular_file: windows_file_write.VerifyRegularFile,
    ) -> None:
        nonlocal replacement_happened
        competitor_handle = windows_file_native.open_handle(
            competitor,
            windows_file_native.DELETE_ACCESS
            | windows_file_native.FILE_READ_ATTRIBUTES,
            windows_file_native.OPEN_EXISTING,
            share_delete=True,
        )
        try:
            windows_file_native.replace_file_by_handle(
                competitor_handle,
                destination,
            )
            replacement_happened = True
        finally:
            windows_file_native.close_handle(competitor_handle)
        real_verify(destination, expected_identity, verify_regular_file)

    monkeypatch.setattr(
        windows_file_write,
        "verify_committed",
        verify_after_competing_replacement,
    )

    with pytest.raises(FileConflictError):
        _ = access.write_file(
            "notes.txt",
            "after",
            expected_sha256=current.sha256,
        )

    assert replacement_happened is True
    assert target.read_text(encoding="utf-8") == "concurrent"
    assert tuple(root.glob(".notes.txt.*.bak")) == ()
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows atomic hard-link semantics")
def test_atomic_create_preserves_a_concurrently_created_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "notes.txt"
    access = ProjectFileAccess(root)
    real_link = windows_file_write.link_file_by_handle

    def create_competing_file(handle: int, destination: Path) -> None:
        target.write_bytes(b"concurrent")
        real_link(handle, destination)

    monkeypatch.setattr(
        windows_file_write,
        "link_file_by_handle",
        create_competing_file,
    )

    with pytest.raises(FileConflictError, match="appeared"):
        _ = access.write_file("notes.txt", "ours", expected_sha256=None)

    assert target.read_text(encoding="utf-8") == "concurrent"
    assert tuple(root.glob(".notes.txt.*.tmp")) == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point semantics")
def test_read_rejects_a_directory_link_to_outside_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "public.txt").write_text("private", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc.winerror}")

    with pytest.raises(UnsafeProjectPathError, match="reparse"):
        ProjectFileAccess(root).read_bytes("linked/public.txt")
