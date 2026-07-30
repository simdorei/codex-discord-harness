from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.messages import (
    FileEntry,
    ListFilesOutput,
    ProjectInfoOutput,
    ReadFileOutput,
    WriteFileOutput,
)

MAX_FILE_BYTES: Final = 1_048_576
MAX_LIST_RESULTS: Final = 500
SENSITIVE_PARTS: Final = frozenset(
    {
        ".aws",
        ".codex-remote-mcp",
        ".env",
        ".git",
        ".npmrc",
        ".ssh",
        "cookies.txt",
        "credentials",
        "id_ed25519",
        "id_rsa",
        "secrets",
        "gcloud",
    }
)
SENSITIVE_BASENAME: Final = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"\.(?:pem|key|p12|pfx|keystore)$", re.IGNORECASE),
    re.compile(r"^id_rsa.*$", re.IGNORECASE),
    re.compile(r"(?:token|secret|credential)", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class ProjectFileError(Exception):
    path: str
    reason: str

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


class UnsafeProjectPathError(ProjectFileError):
    """Raised when a path escapes the project or targets secret material."""


class ProjectFileSizeError(ProjectFileError):
    """Raised when a file exceeds the bridge transfer limit."""


class ProjectFileEncodingError(ProjectFileError):
    """Raised when a file is not UTF-8 text."""


class FileConflictError(ProjectFileError):
    """Raised when an optimistic write could overwrite unseen changes."""


@dataclass(frozen=True, slots=True)
class ProjectFileAccess:
    """Confined UTF-8 file access for one bound project root."""

    root: Path

    def __post_init__(self) -> None:
        resolved = self.root.expanduser().resolve()
        if not resolved.is_dir():
            raise UnsafeProjectPathError(str(resolved), "project root is not a directory")
        object.__setattr__(self, "root", resolved)

    def project_info(self, thread_id: str) -> ProjectInfoOutput:
        return ProjectInfoOutput(root=str(self.root), thread_id=thread_id)

    def resolve_path(self, value: str, *, require_file: bool = True) -> Path:
        """Resolve one non-sensitive path while keeping it inside the project."""
        return self._resolve(value, require_file=require_file)

    def list_files(self, pattern: str = "**/*", limit: int = 200) -> ListFilesOutput:
        bounded_limit = min(max(limit, 1), MAX_LIST_RESULTS)
        entries: list[FileEntry] = []
        matched = sorted(self.root.glob(pattern))
        for candidate in matched:
            if len(entries) >= bounded_limit:
                break
            if not candidate.is_file() or self._is_sensitive(candidate):
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            entries.append(
                FileEntry(
                    path=resolved.relative_to(self.root).as_posix(),
                    size_bytes=resolved.stat().st_size,
                )
            )
        visible_count = sum(
            1
            for candidate in matched
            if candidate.is_file()
            and not self._is_sensitive(candidate)
            and candidate.resolve().is_relative_to(self.root)
        )
        return ListFilesOutput(
            files=tuple(entries),
            truncated=visible_count > len(entries),
        )

    def read_file(self, path: str, *, start_line: int, max_lines: int) -> ReadFileOutput:
        target = self._resolve(path)
        raw = target.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ProjectFileSizeError(path, f"file exceeds {MAX_FILE_BYTES} bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectFileEncodingError(path, "file is not UTF-8 text") from exc
        lines = text.splitlines()
        bounded_start = max(start_line, 1)
        bounded_count = min(max(max_lines, 1), 500)
        start_index = bounded_start - 1
        selected = lines[start_index : start_index + bounded_count]
        end_line = start_index + len(selected)
        selected_content = "\n".join(selected)
        safe_content = redact(selected_content)
        return ReadFileOutput(
            path=target.relative_to(self.root).as_posix(),
            content=safe_content,
            sha256=hashlib.sha256(raw).hexdigest(),
            start_line=bounded_start,
            end_line=end_line,
            total_lines=len(lines),
            truncated=end_line < len(lines),
            redacted=safe_content != selected_content,
        )

    def write_file(
        self,
        path: str,
        content: str,
        *,
        expected_sha256: str | None,
    ) -> WriteFileOutput:
        target = self._resolve(path, require_file=False)
        raw = content.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise ProjectFileSizeError(path, f"content exceeds {MAX_FILE_BYTES} bytes")
        created = not target.exists()
        if created and expected_sha256 is not None:
            raise FileConflictError(path, "new files require expected_sha256=null")
        if not created:
            current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if expected_sha256 is None:
                raise FileConflictError(path, "existing files require expected_sha256")
            if current_hash != expected_sha256:
                raise FileConflictError(path, "file changed since it was read")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, target)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return WriteFileOutput(
            path=target.relative_to(self.root).as_posix(),
            sha256=hashlib.sha256(raw).hexdigest(),
            bytes_written=len(raw),
            created=created,
        )

    def _resolve(self, value: str, *, require_file: bool = True) -> Path:
        relative = Path(value)
        if relative.is_absolute():
            raise UnsafeProjectPathError(value, "absolute paths are not allowed")
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise UnsafeProjectPathError(value, "path is outside the project root")
        if self._is_sensitive(target):
            raise UnsafeProjectPathError(value, "sensitive paths are not exposed")
        if require_file and not target.is_file():
            raise UnsafeProjectPathError(value, "path is not a file")
        return target

    def _is_sensitive(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        parts = tuple(part.casefold() for part in relative.parts)
        basename = relative.name
        return (
            any(part in SENSITIVE_PARTS for part in parts)
            or any(pattern.search(basename) is not None for pattern in SENSITIVE_BASENAME)
        )
