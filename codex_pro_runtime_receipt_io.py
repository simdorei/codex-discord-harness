from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import override

from pydantic import ValidationError

from codex_pro_runtime_receipt_models import RuntimeReceiptSet


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
    finally:
        _ = temp_path.unlink(missing_ok=True)
    return path


__all__ = [
    "RuntimeReceiptError",
    "read_runtime_receipts",
    "write_runtime_receipts",
]
