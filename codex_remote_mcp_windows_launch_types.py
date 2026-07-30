from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_windows_windows import ResolvedWindow


class OwnedProcess(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LaunchedApplication:
    window: ResolvedWindow
    process: OwnedProcess
    temporary_profile: str | None


@dataclass(frozen=True, slots=True)
class FailedLaunchCleanup:
    app: str
    process: OwnedProcess | None
    temporary_profile: str | None


@dataclass(frozen=True, slots=True, init=False)
class ApplicationLaunchCleanupError(ComputerControlError):
    cleanup: FailedLaunchCleanup

    def __init__(self, cleanup: FailedLaunchCleanup) -> None:
        ComputerControlError.__init__(
            self,
            "Failed application cleanup must be retried.",
        )
        object.__setattr__(self, "cleanup", cleanup)
