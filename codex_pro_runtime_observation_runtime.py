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
    BrowserEvidenceSourceError,
    browser_session_binding_sha256,
    read_latest_browser_evidence,
)
from codex_pro_runtime_observation_authority import RuntimeObservationAuthority
from codex_pro_runtime_observation_collector import RuntimeObservationCollector
from codex_pro_runtime_observation_models import (
    RuntimeObservationSnapshot,
)
from codex_pro_runtime_observation_ingress import (
    ActiveRuntimeCycle,
    CapabilityIngressResult,
    RuntimeIngressError,
    RuntimeIngressStatus,
    RuntimeObservationIngress,
    TerminalIngressEvidence,
)
from codex_pro_runtime_observation_release import (
    GitRunner,
    RuntimeReleaseContextResolver,
)
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_observation_receipts import (
    build_observed_runtime_receipts,
)
from codex_pro_runtime_receipt_builders import post_restart_evidence_sha256
from codex_pro_runtime_receipt_io import (
    DEFAULT_RUNTIME_RECEIPT_PATH,
    RuntimeReceiptError,
    publish_runtime_receipts,
    remove_runtime_receipts,
)
from codex_pro_runtime_receipt_models import RuntimeReceiptSet
from codex_pro_resident_identity import (
    DEFAULT_RESIDENT_IDENTITY_KEY_PATH,
    DEFAULT_RESIDENT_IDENTITY_PATH,
    ResidentIdentityError,
    ResidentRuntimeIdentity,
    build_resident_identity,
    load_or_create_identity_key,
    publish_resident_identity,
    remove_resident_identity,
)

SnapshotReader = Callable[[], AppServerLifecycleSnapshot]
Clock = Callable[[], datetime]
ReceiptPublisher = Callable[[RuntimeReceiptSet, Path], Path]
ReceiptRemover = Callable[[Path], None]
ResidentIdentityPublisher = Callable[
    [ResidentRuntimeIdentity, Path, Path], Path
]
ResidentIdentityRemover = Callable[[Path], None]
ResidentIdentityKeyLoader = Callable[[Path], bytes]


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
        receipt_path: Path | None = None,
        receipt_publisher: ReceiptPublisher = publish_runtime_receipts,
        receipt_remover: ReceiptRemover = remove_runtime_receipts,
        resident_identity_path: Path | None = None,
        resident_identity_key_path: Path | None = None,
        resident_identity_publisher: ResidentIdentityPublisher = (
            publish_resident_identity
        ),
        resident_identity_remover: ResidentIdentityRemover = (
            remove_resident_identity
        ),
        resident_identity_key_loader: ResidentIdentityKeyLoader = (
            load_or_create_identity_key
        ),
    ) -> None:
        self._release_resolver = RuntimeReleaseContextResolver(
            release_repo_root,
            git_runner=git_runner,
        )
        self._snapshot_reader = snapshot_reader
        self._evidence_dir = evidence_dir
        self._clock = clock
        self._receipt_path = receipt_path or (
            release_repo_root / DEFAULT_RUNTIME_RECEIPT_PATH
        )
        self._receipt_publisher = receipt_publisher
        self._receipt_remover = receipt_remover
        self._resident_identity_path = resident_identity_path or (
            release_repo_root / DEFAULT_RESIDENT_IDENTITY_PATH
        )
        self._resident_identity_key_path = resident_identity_key_path or (
            release_repo_root / DEFAULT_RESIDENT_IDENTITY_KEY_PATH
        )
        self._resident_identity_publisher = resident_identity_publisher
        self._resident_identity_remover = resident_identity_remover
        self._resident_identity_key_loader = resident_identity_key_loader
        self._authority = RuntimeObservationAuthority()
        self._collector = RuntimeObservationCollector(self._authority)
        self._ingress = RuntimeObservationIngress(
            authority=self._authority,
            collector=self._collector,
            evidence_dir=evidence_dir,
        )
        self._lock = threading.Lock()
        self._active_cycle: ActiveRuntimeCycle | None = None
        self._active_runtime_status: ProRuntimeStatus | None = None
        self._receipt_emitted = False
        self._receipt_error: str | None = None

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
            try:
                self._receipt_remover(self._receipt_path)
                self._resident_identity_remover(
                    self._resident_identity_path
                )
            except (
                OSError,
                ResidentIdentityError,
                RuntimeReceiptError,
            ) as exc:
                raise RuntimeObservationStartError(
                    "previous runtime receipts could not be invalidated"
                ) from exc
            _ = self._collector.reset()
            self._active_cycle = None
            self._active_runtime_status = None
            self._receipt_emitted = False
            self._receipt_error = None
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
            self._active_cycle = ActiveRuntimeCycle(
                release=release,
                codex_session_binding_sha256=browser_session_binding_sha256(
                    codex_session_id
                ),
                cycle_binding_sha256=self._authority.cycle_binding_sha256(),
            )
            self._active_runtime_status = runtime_status
            return RuntimeCycleStart.STARTED

    def bind_route(
        self,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
    ) -> RuntimeIngressStatus:
        with self._lock:
            if self._active_cycle is None:
                return RuntimeIngressStatus.NOT_APPLICABLE
            return self._ingress.bind_route(
                self._active_cycle,
                thread_id=thread_id,
                computer_session_id=computer_session_id,
                session_binding_sha256=session_binding_sha256,
            )

    def commit_capability(
        self,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
        inventory_sha256: str,
        tool_count: int,
        terminal_execute_present: bool,
        terminal_interact_present: bool,
    ) -> CapabilityIngressResult:
        with self._lock:
            if self._active_cycle is None:
                return CapabilityIngressResult(RuntimeIngressStatus.NOT_APPLICABLE)
            try:
                return self._ingress.commit_capability(
                    self._active_cycle,
                    thread_id=thread_id,
                    computer_session_id=computer_session_id,
                    session_binding_sha256=session_binding_sha256,
                    inventory_sha256=inventory_sha256,
                    tool_count=tool_count,
                    terminal_execute_present=terminal_execute_present,
                    terminal_interact_present=terminal_interact_present,
                    now=self._clock(),
                )
            except (
                BrowserEvidenceSourceError,
                OSError,
                OverflowError,
                RuntimeIngressError,
                ValueError,
            ) as exc:
                _ = self._collector.invalidate("runtime_capability_invalid")
                raise RuntimeObservationStartError(
                    "runtime capability observation failed: "
                    + type(exc).__name__
                ) from exc

    def observe_terminal(
        self,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
        cycle_binding_sha256: str | None,
        evidence: TerminalIngressEvidence,
    ) -> RuntimeIngressStatus:
        with self._lock:
            if self._active_cycle is None:
                return RuntimeIngressStatus.NOT_APPLICABLE
            if self._receipt_emitted:
                return RuntimeIngressStatus.NOT_APPLICABLE
            result = self._ingress.observe_terminal(
                self._active_cycle,
                thread_id=thread_id,
                computer_session_id=computer_session_id,
                session_binding_sha256=session_binding_sha256,
                cycle_binding_sha256=cycle_binding_sha256,
                evidence=evidence,
            )
            if (
                result is RuntimeIngressStatus.ACCEPTED
                and self._collector.snapshot().ready
            ):
                self._publish_ready_receipts_locked()
            return result

    def invalidate_active(self, failure_code: str) -> None:
        with self._lock:
            if self._active_cycle is not None and not self._receipt_emitted:
                _ = self._collector.invalidate(failure_code)

    def read_browser_proof(self) -> BrowserEvidenceProof:
        with self._lock:
            snapshot = self._collector.snapshot()
            cycle = self._active_cycle
            if (
                snapshot.phase != "waiting_browser"
                or snapshot.last_recorded_at is None
                or cycle is None
            ):
                raise RuntimeObservationStartError(
                    "runtime observation is not waiting for Browser evidence"
                )
            return read_latest_browser_evidence(
                self._evidence_dir,
                expected_session_binding_sha256=(
                    cycle.codex_session_binding_sha256
                ),
                not_before=snapshot.last_recorded_at,
                now=self._clock(),
            )

    def snapshot(self) -> RuntimeObservationSnapshot:
        with self._lock:
            return self._collector.snapshot().model_copy(
                update={
                    "receipt_emitted": self._receipt_emitted,
                    "receipt_error": self._receipt_error,
                }
            )

    def _publish_ready_receipts_locked(self) -> None:
        if self._receipt_emitted:
            return
        try:
            observations = self._collector.finalized_observations()
            runtime_status = self._active_runtime_status
            if runtime_status is None:
                raise RuntimeError("resident runtime identity is unavailable")
            snapshot = self._snapshot_reader()
            if (
                not snapshot.healthy
                or snapshot.generation != runtime_status.resident_generation
                or snapshot.accepting_since
                != runtime_status.resident_accepting_since
                or snapshot.plugin_runtime_fingerprint
                != runtime_status.resident_plugin_fingerprint
                or snapshot.process_id != runtime_status.resident_process_id
                or snapshot.process_identity
                != runtime_status.resident_process_identity
            ):
                raise RuntimeError("resident runtime changed before publication")
            key = self._resident_identity_key_loader(
                self._resident_identity_key_path
            )
            identity = build_resident_identity(
                runtime_status,
                recorded_at=self._clock(),
                key=key,
            )
            receipts = build_observed_runtime_receipts(
                observations,
                current_resident=identity,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._receipt_error = type(exc).__name__
            _ = self._collector.invalidate("runtime_receipt_build_invalid")
            return
        try:
            _ = self._resident_identity_publisher(
                identity,
                self._resident_identity_path,
                self._resident_identity_key_path,
            )
            _ = self._receipt_publisher(receipts, self._receipt_path)
        except (
            OSError,
            ResidentIdentityError,
            RuntimeReceiptError,
        ) as exc:
            self._receipt_error = type(exc).__name__
            return
        self._receipt_emitted = True
        self._receipt_error = None

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


def _invalidate_persisted_runtime_evidence() -> None:
    root = Path(__file__).resolve().parent
    remove_runtime_receipts(root / DEFAULT_RUNTIME_RECEIPT_PATH)
    remove_resident_identity(root / DEFAULT_RESIDENT_IDENTITY_PATH)


app_server_transport.DEFAULT_CLIENT.add_resident_invalidation_observer(
    _invalidate_persisted_runtime_evidence
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


def bind_pro_runtime_observation_route(
    *,
    thread_id: str,
    computer_session_id: str,
    session_binding_sha256: str,
) -> RuntimeIngressStatus:
    return _DEFAULT_RUNTIME.bind_route(
        thread_id=thread_id,
        computer_session_id=computer_session_id,
        session_binding_sha256=session_binding_sha256,
    )


def commit_pro_runtime_capability(
    *,
    thread_id: str,
    computer_session_id: str,
    session_binding_sha256: str,
    inventory_sha256: str,
    tool_count: int,
    terminal_execute_present: bool,
    terminal_interact_present: bool,
) -> CapabilityIngressResult:
    return _DEFAULT_RUNTIME.commit_capability(
        thread_id=thread_id,
        computer_session_id=computer_session_id,
        session_binding_sha256=session_binding_sha256,
        inventory_sha256=inventory_sha256,
        tool_count=tool_count,
        terminal_execute_present=terminal_execute_present,
        terminal_interact_present=terminal_interact_present,
    )


def observe_pro_runtime_terminal(
    *,
    thread_id: str,
    computer_session_id: str,
    session_binding_sha256: str,
    cycle_binding_sha256: str | None,
    evidence: TerminalIngressEvidence,
) -> RuntimeIngressStatus:
    return _DEFAULT_RUNTIME.observe_terminal(
        thread_id=thread_id,
        computer_session_id=computer_session_id,
        session_binding_sha256=session_binding_sha256,
        cycle_binding_sha256=cycle_binding_sha256,
        evidence=evidence,
    )


def invalidate_pro_runtime_observation(failure_code: str) -> None:
    _DEFAULT_RUNTIME.invalidate_active(failure_code)


__all__ = [
    "RuntimeCycleStart",
    "RuntimeObservationRuntime",
    "RuntimeObservationStartError",
    "bind_pro_runtime_observation_route",
    "commit_pro_runtime_capability",
    "invalidate_pro_runtime_observation",
    "observe_pro_runtime_terminal",
    "start_pro_runtime_observation_cycle",
]
