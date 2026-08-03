from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import override

from pydantic import ValidationError

from codex_pro_runtime_receipt_models import RuntimeReceiptSet
from codex_pro_runtime_file_lock import RuntimeFileLockError, runtime_file_lock

DEFAULT_RUNTIME_RECEIPT_PATH = Path(
    ".release-evidence/pro-runtime-receipts.json"
)


class RuntimeReceiptError(ValueError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "runtime receipts are invalid"


def read_runtime_receipts(path: Path) -> RuntimeReceiptSet:
    try:
        return RuntimeReceiptSet.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise RuntimeReceiptError(
            "runtime receipt file is unavailable or malformed"
        ) from exc


def write_runtime_receipts(receipts: RuntimeReceiptSet, path: Path) -> Path:
    payload = receipts.model_dump_json(indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            _ = target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        _ = temp_path.replace(path)
        _sync_directory(path.parent)
    finally:
        _ = temp_path.unlink(missing_ok=True)
    return path


def publish_runtime_receipts(receipts: RuntimeReceiptSet, path: Path) -> Path:
    """Publish one canonical set without replacing evidence from another cycle."""

    try:
        with runtime_file_lock(path.with_name(path.name + ".lock")):
            if path.exists():
                existing = read_runtime_receipts(path)
                if existing != receipts:
                    raise RuntimeReceiptError(
                        "runtime receipt file belongs to a different observation cycle"
                    )
                return path
            _ = write_runtime_receipts(receipts, path)
            if read_runtime_receipts(path) != receipts:
                raise RuntimeReceiptError(
                    "runtime receipt publication verification failed"
                )
    except RuntimeFileLockError as exc:
        raise RuntimeReceiptError("runtime receipt publication lock failed") from exc
    return path


def remove_runtime_receipts(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeReceiptError("previous runtime receipts could not be cleared") from exc


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "DEFAULT_RUNTIME_RECEIPT_PATH",
    "RuntimeReceiptError",
    "publish_runtime_receipts",
    "read_runtime_receipts",
    "remove_runtime_receipts",
    "write_runtime_receipts",
]
