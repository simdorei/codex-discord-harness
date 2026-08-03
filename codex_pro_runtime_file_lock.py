from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class RuntimeFileLockError(RuntimeError):
    pass


@contextmanager
def runtime_file_lock(path: Path) -> Generator[None, None, None]:
    """Hold an OS advisory lock; the inert lock file may safely persist."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b", buffering=0) as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            _ = handle.write(b"\0")
        handle.seek(0)
        try:
            _lock(handle.fileno())
        except OSError as exc:
            raise RuntimeFileLockError("runtime evidence lock failed") from exc
        try:
            yield
        finally:
            try:
                _unlock(handle.fileno())
            except OSError as exc:
                raise RuntimeFileLockError(
                    "runtime evidence unlock failed"
                ) from exc


def _lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


__all__ = ["RuntimeFileLockError", "runtime_file_lock"]
