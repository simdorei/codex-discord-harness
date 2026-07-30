from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from codex_remote_mcp_computer_contracts import (
    ComputerActionPermit,
    ComputerWindowIdentity,
)
from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_windows_launch_types import FailedLaunchCleanup, OwnedProcess

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OwnedWindow:
    process_id: int
    process_path: str
    process_name: str
    process: OwnedProcess


@dataclass(frozen=True, slots=True)
class OwnedLaunch:
    window_id: int
    owner: OwnedWindow
    process: OwnedProcess
    temporary_profile: str | None


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    completed_launches: tuple[OwnedLaunch, ...]
    completed_failed_launches: tuple[FailedLaunchCleanup, ...]
    failures: tuple[ComputerControlError, ...]


@dataclass(frozen=True, slots=True)
class OwnedActionPermit:
    source: ComputerActionPermit
    verify_owned_process: Callable[[], OwnedLaunch]

    @property
    def identity(self) -> ComputerWindowIdentity:
        return self.source.identity

    def require_active(self) -> None:
        self.source.require_active()
        _ = self.verify_owned_process()

    def require_owned_launch(self) -> OwnedLaunch:
        self.source.require_active()
        return self.verify_owned_process()


def require_live_owned_launch(
    owner: OwnedWindow,
    launch: OwnedLaunch | None,
) -> OwnedLaunch:
    if (
        launch is None
        or launch.owner is not owner
        or launch.process is not owner.process
        or owner.process.pid != owner.process_id
    ):
        raise ComputerControlError("The launched process identity changed.")
    try:
        exit_code = owner.process.poll()
    except OSError as exc:
        raise ComputerControlError(
            "Windows could not verify the launched process."
        ) from exc
    if exit_code is not None:
        raise ComputerControlError("The launched process is no longer running.")
    return launch


def owned_launch_exit_code(launch: OwnedLaunch) -> int | None:
    owner = launch.owner
    if launch.process is not owner.process or owner.process.pid != owner.process_id:
        raise ComputerControlError("The launched process identity changed.")
    try:
        return owner.process.poll()
    except OSError as exc:
        raise ComputerControlError(
            "Windows could not verify the launched process."
        ) from exc


def cleanup_owned_applications(
    launched: tuple[OwnedLaunch, ...],
    failed_launches: tuple[FailedLaunchCleanup, ...],
    stop_process: Callable[[int, OwnedProcess, str], None],
    remove_profile: Callable[[str | None], None],
    retry_failed: Callable[[FailedLaunchCleanup], None],
) -> CleanupOutcome:
    completed_launches: list[OwnedLaunch] = []
    completed_failed: list[FailedLaunchCleanup] = []
    failures: list[ComputerControlError] = []
    for cleanup in failed_launches:
        try:
            retry_failed(cleanup)
        except ComputerControlError as exc:
            failures.append(exc)
        else:
            completed_failed.append(cleanup)
    for launch in launched:
        try:
            stop_process(launch.window_id, launch.process, launch.owner.process_path)
            remove_profile(launch.temporary_profile)
        except ComputerControlError as exc:
            failures.append(exc)
        else:
            completed_launches.append(launch)
    return CleanupOutcome(
        completed_launches=tuple(completed_launches),
        completed_failed_launches=tuple(completed_failed),
        failures=tuple(failures),
    )


def prune_exited_launches(
    launched: tuple[OwnedLaunch, ...],
    remove_profile: Callable[[str | None], None],
) -> tuple[OwnedLaunch, ...]:
    exited: list[OwnedLaunch] = []
    for launch in launched:
        try:
            if launch.process.poll() is None:
                continue
            remove_profile(launch.temporary_profile)
        except (OSError, ComputerControlError) as exc:
            LOGGER.warning(
                "remote_owned_process_prune_deferred error=%s",
                type(exc).__name__,
            )
            continue
        exited.append(launch)
    return tuple(exited)
