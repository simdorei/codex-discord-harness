from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Final

from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_windows_launch_types import (
    ApplicationLaunchCleanupError,
    FailedLaunchCleanup,
    OwnedProcess,
)

LOGGER = logging.getLogger(__name__)
PROFILE_CLEANUP_ATTEMPTS: Final = 60
PROFILE_CLEANUP_DELAY_SECONDS: Final = 1.0


def remove_temporary_profile(directory: str | None) -> None:
    if not _cleanup_profile(directory):
        raise ComputerControlError(
            "The isolated Chrome profile could not be removed yet."
        )


def retry_failed_launch_cleanup(
    cleanup: FailedLaunchCleanup,
    remove_profile: Callable[[str | None], None],
) -> None:
    failures: list[ComputerControlError] = []
    if cleanup.process is not None:
        try:
            _terminate_process(cleanup.process)
        except ComputerControlError as exc:
            failures.append(exc)
    try:
        remove_profile(cleanup.temporary_profile)
    except ComputerControlError as exc:
        failures.append(exc)
    if failures:
        raise ApplicationLaunchCleanupError(cleanup) from failures[0]


def _terminate_process(process: OwnedProcess) -> None:
    if _process_exit_code(process) is not None:
        return
    try:
        process.terminate()
        _ = process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            _ = process.wait(timeout=3)
        except subprocess.TimeoutExpired as exc:
            raise ComputerControlError(
                "Stopping the failed application launch timed out."
            ) from exc
        except OSError as exc:
            raise ComputerControlError(
                "Windows could not stop the failed application launch."
            ) from exc
    except OSError as exc:
        raise ComputerControlError(
            "Windows could not stop the failed application launch."
        ) from exc
    if _process_exit_code(process) is None:
        raise ComputerControlError("The failed application launch is still running.")


def _process_exit_code(process: OwnedProcess) -> int | None:
    try:
        return process.poll()
    except OSError as exc:
        raise ComputerControlError(
            "Windows could not inspect the failed application launch."
        ) from exc


def _cleanup_profile(directory: str | None) -> bool:
    if directory is None:
        return True
    for attempt in range(PROFILE_CLEANUP_ATTEMPTS):
        try:
            shutil.rmtree(directory)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            if attempt + 1 == PROFILE_CLEANUP_ATTEMPTS:
                LOGGER.warning(
                    "remote_chrome_profile_cleanup_failed error=%s attempts=%s",
                    type(exc).__name__,
                    PROFILE_CLEANUP_ATTEMPTS,
                )
                return False
            time.sleep(PROFILE_CLEANUP_DELAY_SECONDS)
    return False
