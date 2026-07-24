from __future__ import annotations

# pyright: reportAny=false

import ctypes
import os
from typing import Final, final


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_PROCESS_TERMINATE: Final = 0x0001
_PROCESS_SET_QUOTA: Final = 0x0100
_TH32CS_SNAPTHREAD: Final = 0x00000004
_THREAD_SUSPEND_RESUME: Final = 0x0002
_WAIT_OBJECT_0: Final = 0
_WAIT_TIMEOUT: Final = 0x00000102
_WAIT_FAILED: Final = 0xFFFFFFFF
_INFINITE_FAILURE: Final = 0xFFFFFFFF
_JOB_TERMINATION_TIMEOUT_MS: Final = 5000


@final
class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


@final
class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


@final
class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


@final
class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32),
        ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
    ]


@final
class WindowsKillOnCloseJob:
    """Own one Windows process tree until termination is confirmed."""

    __slots__ = ("_handle",)

    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle

    @property
    def closed(self) -> bool:
        return self._handle is None

    def terminate_and_close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        if not kernel32.TerminateJobObject(handle, 1):
            raise _windows_error("TerminateJobObject failed")
        wait_result = int(kernel32.WaitForSingleObject(handle, _JOB_TERMINATION_TIMEOUT_MS))
        if wait_result == _WAIT_TIMEOUT:
            raise TimeoutError("Timed out waiting for the app-server Windows Job Object to empty.")
        if wait_result == _WAIT_FAILED:
            raise _windows_error("WaitForSingleObject failed for app-server job")
        if wait_result != _WAIT_OBJECT_0:
            raise OSError(f"Unexpected app-server job wait result: {wait_result}")
        if not kernel32.CloseHandle(handle):
            raise _windows_error("CloseHandle failed for app-server job")
        self._handle = None


def create_kill_on_close_job_for_suspended_process(process_id: int) -> WindowsKillOnCloseJob:
    if os.name != "nt":
        raise OSError("Windows Job Objects are only available on Windows.")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    raw_job = kernel32.CreateJobObjectW(None, None)
    if not raw_job:
        raise _windows_error("CreateJobObjectW failed")
    job_handle = int(raw_job)
    assigned = False
    try:
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise _windows_error("SetInformationJobObject failed")
        process_handle = kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA,
            False,
            process_id,
        )
        if not process_handle:
            raise _windows_error(f"OpenProcess failed for suspended app-server PID {process_id}")
        try:
            if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
                raise _windows_error(
                    f"AssignProcessToJobObject failed for app-server PID {process_id}"
                )
            assigned = True
        finally:
            _ = kernel32.CloseHandle(process_handle)
        _resume_suspended_process(process_id)
        return WindowsKillOnCloseJob(job_handle)
    except (OSError, TimeoutError):
        if assigned:
            kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.TerminateJobObject.restype = ctypes.c_int
            _ = kernel32.TerminateJobObject(job_handle, 1)
        _ = kernel32.CloseHandle(job_handle)
        raise


def _resume_suspended_process(process_id: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Thread32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32First.restype = ctypes.c_int
    kernel32.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32Next.restype = ctypes.c_int
    kernel32.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel32.ResumeThread.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid_handle):
        raise _windows_error("Thread snapshot failed for suspended app-server")
    entry = _ThreadEntry32()
    entry.dwSize = ctypes.sizeof(entry)
    resumed = False
    try:
        has_entry = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while has_entry:
            if int(entry.th32OwnerProcessID) == process_id:
                thread_handle = kernel32.OpenThread(
                    _THREAD_SUSPEND_RESUME,
                    False,
                    int(entry.th32ThreadID),
                )
                if not thread_handle:
                    raise _windows_error(
                        f"OpenThread failed for suspended app-server PID {process_id}"
                    )
                try:
                    if int(kernel32.ResumeThread(thread_handle)) == _INFINITE_FAILURE:
                        raise _windows_error(
                            f"ResumeThread failed for suspended app-server PID {process_id}"
                        )
                    resumed = True
                finally:
                    _ = kernel32.CloseHandle(thread_handle)
                break
            has_entry = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        _ = kernel32.CloseHandle(snapshot)
    if not resumed:
        raise OSError(f"No primary thread found for suspended app-server PID {process_id}.")


def _windows_error(message: str) -> OSError:
    return OSError(ctypes.get_last_error(), message)
