from __future__ import annotations

import ctypes
import os
import sys

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def current_process_identity() -> str:
    return process_identity(os.getpid())


def process_identity(process_id: int) -> str:
    """Return a PID-reuse-safe identity for a live local process."""

    if process_id <= 0:
        raise OSError("process ID must be positive")
    if os.name == "nt":
        return _windows_process_identity(process_id)
    if sys.platform.startswith("linux"):
        return _linux_process_identity(process_id)
    raise OSError("process identity is unsupported on this platform")


def _windows_process_identity(process_id: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint64),
        ctypes.POINTER(ctypes.c_uint64),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        0,
        process_id,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    created = ctypes.c_uint64()
    exited = ctypes.c_uint64()
    kernel = ctypes.c_uint64()
    user = ctypes.c_uint64()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
    finally:
        _ = kernel32.CloseHandle(handle)
    return f"{process_id}|{created.value}"


def _linux_process_identity(process_id: int) -> str:
    stat = (f"/proc/{process_id}/stat")
    raw = open(stat, encoding="utf-8").read()  # noqa: SIM115 - one bounded proc read.
    closing = raw.rfind(")")
    if closing < 0:
        raise OSError("process stat is malformed")
    fields = raw[closing + 2 :].split()
    if len(fields) <= 19:
        raise OSError("process stat has no start time")
    return f"{process_id}|{fields[19]}"


__all__ = ["current_process_identity", "process_identity"]
