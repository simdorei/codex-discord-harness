from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum, unique
from pathlib import Path
from typing import final, override

import codex_app_server_transport as app_server_transport
from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_pro_browser_evidence_source import (
    BrowserEvidenceProof,
    browser_session_binding_sha256,
    read_latest_browser_evidence,
)
from codex_pro_runtime_observation_authority import RuntimeObservationAuthority
from codex_pro_runtime_observation_collector import RuntimeObservationCollector
from codex_pro_runtime_observation_models import (
    RuntimeObservationSnapshot,
)
from codex_pro_runtime_observation_release import (
    GitRunner,
    RuntimeReleaseContextResolver,
)
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_builders import post_restart_evidence_sha256

SnapshotReader = Callable[[], AppServerLifecycleSnapshot]
Clock = Callable[[], datetime]


@unique
class RuntimeCycleStart(StrEnum):
    STARTED = "started"
    NOT_APPLICABLE = "not_applicable"


class RuntimeObservationStartError(RuntimeError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "runtime observation could not start"


@final
class RuntimeObservationRuntime:  # MUTABLE_OK: synchronized process singleton.
    def __init__(
        self,
        *,
        release_repo_root: Path,
        snapshot_reader: SnapshotReader,
        evidence_dir: Path,
        clock: Clock = lambda: datetime.now(UTC),
        git_runner: GitRunner | None = None,
    ) -> None:
        self._release_resolver = RuntimeReleaseContextResolver(
            release_repo_root,
            git_runner=git_runner,
        )
        self._snapshot_reader = snapshot_reader
        self._evidence_dir = evidence_dir
        self._clock = clock
        self._authority = RuntimeObservationAuthority()
        self._collector = RuntimeObservationCollector(self._authority)
        self._lock = threading.Lock()
        self._browser_session_binding: str | None = None

    def start_cycle(
        self,
        runtime_status: ProRuntimeStatus,
        project_root: Path,
        codex_session_id: str,
    ) -> RuntimeCycleStart:
        try:
            return self._start_cycle(runtime_status, project_root, codex_session_id)
        except RuntimeObservationStartError:
            raise
        except (
            OSError,
            OverflowError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise RuntimeObservationStartError(
                f"runtime observation validation failed: {type(exc).__name__}"
            ) from exc

    def _start_cycle(
        self,
        runtime_status: ProRuntimeStatus,
        project_root: Path,
        codex_session_id: str,
    ) -> RuntimeCycleStart:
        with self._lock:
            release_before = self._release_resolver.resolve(
                project_root,
                runtime_status,
            )
            if release_before is None:
                return RuntimeCycleStart.NOT_APPLICABLE
            snapshot = self._snapshot_reader()
            if (
                not snapshot.healthy
                or snapshot.generation != runtime_status.resident_generation
                or snapshot.accepting_since
                != runtime_status.resident_accepting_since
                or snapshot.plugin_runtime_fingerprint
                != runtime_status.resident_plugin_fingerprint
            ):
                raise RuntimeObservationStartError(
                    "resident runtime changed after !pro preflight"
                )
            if (
                snapshot.accepting_since is None
                or snapshot.plugin_runtime_fingerprint is None
            ):
                raise RuntimeObservationStartError(
                    "resident runtime evidence is unavailable"
                )
            release = self._release_resolver.resolve(project_root, runtime_status)
            if release is None or release != release_before:
                raise RuntimeObservationStartError(
                    "release repository changed while observation was starting"
                )
            recorded_at = self._clock()
            if recorded_at.tzinfo is None:
                raise RuntimeObservationStartError("runtime observation clock is naive")
            resident_started_at = datetime.fromtimestamp(
                snapshot.accepting_since,
                UTC,
            )
            evidence_sha256 = post_restart_evidence_sha256(
                resident_generation=snapshot.generation,
                resident_started_at=resident_started_at,
                plugin_fingerprint_sha256=snapshot.plugin_runtime_fingerprint,
                browser_plugin_version=runtime_status.browser_plugin_version,
            )
            _ = self._collector.reset()
            observation = self._authority.post_restart(
                release,
                evidence_sha256=evidence_sha256,
                recorded_at=recorded_at,
                resident_generation=snapshot.generation,
                resident_started_at=resident_started_at,
                plugin_fingerprint_sha256=snapshot.plugin_runtime_fingerprint,
                browser_plugin_version=runtime_status.browser_plugin_version,
            )
            result = self._collector.observe_post_restart(observation)
            if result.phase != "waiting_browser":
                raise RuntimeObservationStartError(
                    "post-restart runtime observation was rejected"
                )
            self._browser_session_binding = browser_session_binding_sha256(
                codex_session_id
            )
            return RuntimeCycleStart.STARTED

    def read_browser_proof(self) -> BrowserEvidenceProof:
        with self._lock:
            snapshot = self._collector.snapshot()
            binding = self._browser_session_binding
            if (
                snapshot.phase != "waiting_browser"
                or snapshot.last_recorded_at is None
                or binding is None
            ):
                raise RuntimeObservationStartError(
                    "runtime observation is not waiting for Browser evidence"
                )
            return read_latest_browser_evidence(
                self._evidence_dir,
                expected_session_binding_sha256=binding,
                not_before=snapshot.last_recorded_at,
                now=self._clock(),
            )

    def snapshot(self) -> RuntimeObservationSnapshot:
        return self._collector.snapshot()

def _codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


_DEFAULT_RUNTIME = RuntimeObservationRuntime(
    release_repo_root=Path(__file__).resolve().parent,
    snapshot_reader=app_server_transport.DEFAULT_CLIENT.lifecycle_snapshot,
    evidence_dir=(
        _codex_home()
        / "plugins/data/codex-discord-remote-codex-discord-remote/browser-evidence"
    ),
)


def start_pro_runtime_observation_cycle(
    runtime_status: ProRuntimeStatus,
    project_root: Path,
    codex_session_id: str,
) -> RuntimeCycleStart:
    return _DEFAULT_RUNTIME.start_cycle(
        runtime_status,
        project_root,
        codex_session_id,
    )


__all__ = [
    "RuntimeCycleStart",
    "RuntimeObservationRuntime",
    "RuntimeObservationStartError",
    "start_pro_runtime_observation_cycle",
]
