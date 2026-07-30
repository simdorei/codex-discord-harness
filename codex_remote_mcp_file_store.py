from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import final

from codex_remote_mcp_windows_file_guard import (
    WindowsFileConflictError,
    WindowsProjectFileGuard,
)


class ProjectFileStoreError(OSError):
    """Raised when the backend cannot prove project-path confinement."""


class ProjectFileStoreConflict(ProjectFileStoreError):
    """Raised when a retained file no longer meets its write condition."""


@final
class ProjectFileStore:
    """Use retained Windows handles or resolved POSIX paths for file access."""

    __slots__ = ("_windows", "root")

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        try:
            self._windows: WindowsProjectFileGuard | None = (
                WindowsProjectFileGuard(root) if os.name == "nt" else None
            )
        except OSError as exc:
            raise ProjectFileStoreError(str(exc)) from exc

    def ensure(self, relative: Path, *, require_file: bool) -> Path:
        if self._windows is not None:
            try:
                return self._windows.ensure(relative, require_file=require_file)
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise ProjectFileStoreError("path is outside the project root")
        if require_file and not target.is_file():
            raise ProjectFileStoreError("path is not a file")
        return target

    def verify_root(self) -> None:
        if self._windows is None:
            return
        try:
            self._windows.verify_root()
        except OSError as exc:
            raise ProjectFileStoreError(str(exc)) from exc

    def read_bytes(self, relative: Path) -> bytes:
        if self._windows is not None:
            try:
                return self._windows.read_bytes(relative)
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        return self.ensure(relative, require_file=True).read_bytes()

    def file_exists(self, relative: Path) -> bool:
        if self._windows is not None:
            try:
                return self._windows.file_exists(relative)
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        return self.ensure(relative, require_file=False).is_file()

    def file_size(self, relative: Path) -> int:
        if self._windows is not None:
            try:
                return self._windows.file_size(relative)
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        target = self.ensure(relative, require_file=True)
        return target.stat().st_size

    def write_bytes(
        self,
        relative: Path,
        content: bytes,
        *,
        expected_sha256: str | None,
    ) -> bool:
        if self._windows is not None:
            try:
                return self._windows.write_bytes(
                    relative,
                    content,
                    expected_sha256=expected_sha256,
                )
            except WindowsFileConflictError as exc:
                raise ProjectFileStoreConflict(str(exc)) from exc
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        target = self.ensure(relative, require_file=False)
        created = not target.exists()
        if created and expected_sha256 is not None:
            raise ProjectFileStoreConflict("new files require expected_sha256=null")
        if not created:
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_sha256 is None:
                raise ProjectFileStoreConflict("existing files require expected_sha256")
            if current != expected_sha256:
                raise ProjectFileStoreConflict("file changed since it was read")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        return created

    def delete_file(self, relative: Path, *, expected_sha256: str) -> None:
        if self._windows is not None:
            try:
                self._windows.delete_file(
                    relative,
                    expected_sha256=expected_sha256,
                )
                return
            except WindowsFileConflictError as exc:
                raise ProjectFileStoreConflict(str(exc)) from exc
            except OSError as exc:
                raise ProjectFileStoreError(str(exc)) from exc
        target = self.ensure(relative, require_file=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
            raise ProjectFileStoreConflict("file changed since it was read")
        target.unlink()


def _atomic_write(target: Path, content: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            _ = handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
