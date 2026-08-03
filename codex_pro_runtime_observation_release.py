from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import final, override

from codex_pro_runtime_observation_models import RuntimeObservationRelease
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_builders import capability_inventory_sha256
from codex_pro_runtime_receipt_models import EXPECTED_BRIDGE_PROTOCOL_VERSION
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    EXPECTED_TOOL_NAMES,
    build_capability_inventory,
)

GitRunner = Callable[[Sequence[str], Path], tuple[int, str]]


class RuntimeReleaseContextError(RuntimeError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "release context is invalid"


@final
class RuntimeReleaseContextResolver:
    def __init__(
        self,
        release_repo_root: Path,
        *,
        git_runner: GitRunner | None = None,
    ) -> None:
        self._release_repo_root = release_repo_root.resolve()
        self._git_runner = git_runner or _run_git

    def resolve(
        self,
        project_root: Path,
        runtime_status: ProRuntimeStatus,
    ) -> RuntimeObservationRelease | None:
        release_common = self._git_path(self._release_repo_root, "--git-common-dir")
        if release_common is None:
            raise RuntimeReleaseContextError(
                "release repository identity is unavailable"
            )
        project_common = self._git_path(project_root, "--git-common-dir")
        if project_common is None:
            return None
        if os.path.normcase(str(release_common)) != os.path.normcase(str(project_common)):
            return None
        revision = self._git_value(project_root, "HEAD")
        if revision is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
            raise RuntimeReleaseContextError(
                "release repository revision is invalid"
            )
        inventory = build_capability_inventory(EXPECTED_TOOL_NAMES)
        return RuntimeObservationRelease(
            repository_revision=revision,
            plugin_version=runtime_status.remote_plugin_version,
            protocol_version=EXPECTED_BRIDGE_PROTOCOL_VERSION,
            inventory_sha256=capability_inventory_sha256(inventory),
        )

    def _git_path(self, root: Path, value: str) -> Path | None:
        raw = self._git_value(root, value)
        if raw is None:
            return None
        path = Path(raw)
        return (path if path.is_absolute() else root / path).resolve()

    def _git_value(self, root: Path, value: str) -> str | None:
        code, output = self._git_runner(("git", "rev-parse", value), root)
        return output.strip() if code == 0 and output.strip() else None


def _run_git(command: Sequence[str], cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return completed.returncode, completed.stdout


__all__ = [
    "GitRunner",
    "RuntimeReleaseContextError",
    "RuntimeReleaseContextResolver",
]
