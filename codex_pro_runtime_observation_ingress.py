from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from pathlib import Path
from typing import cast, final, override

from codex_pro_browser_evidence_source import (
    browser_session_binding_sha256,
    read_latest_browser_evidence,
)
from codex_pro_runtime_observation_authority import RuntimeObservationAuthority
from codex_pro_runtime_observation_collector import RuntimeObservationCollector
from codex_pro_runtime_observation_models import (
    RuntimeObservationPhase,
    RuntimeObservationRelease,
)
from codex_pro_runtime_receipt_models import TerminalToolAction
from simdorei_mcp_common.runtime_provenance import (
    ObservedTerminalTool,
    runtime_route_binding_sha256,
    terminal_observation_sha256,
    terminal_runtime_evidence_sha256,
)


@unique
class RuntimeIngressStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_APPLICABLE = "not_applicable"
    REJECTED = "rejected"


class RuntimeIngressError(RuntimeError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "runtime provenance was rejected"


@dataclass(slots=True)
class ActiveRuntimeCycle:
    release: RuntimeObservationRelease
    codex_session_binding_sha256: str
    cycle_binding_sha256: str
    route_binding_sha256: str | None = None
    session_binding_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityIngressResult:
    status: RuntimeIngressStatus
    cycle_binding_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class TerminalIngressEvidence:
    tool_name: ObservedTerminalTool
    observation_id: str
    identity_digest: str
    recorded_at: datetime
    expected_observation_id: str | None = None


@final
class RuntimeObservationIngress:
    def __init__(
        self,
        *,
        authority: RuntimeObservationAuthority,
        collector: RuntimeObservationCollector,
        evidence_dir: Path,
    ) -> None:
        self._authority = authority
        self._collector = collector
        self._evidence_dir = evidence_dir

    def bind_route(
        self,
        cycle: ActiveRuntimeCycle,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
    ) -> RuntimeIngressStatus:
        if not self._thread_matches(cycle, thread_id):
            return RuntimeIngressStatus.NOT_APPLICABLE
        binding = runtime_route_binding_sha256(
            thread_id,
            computer_session_id,
            session_binding_sha256,
        )
        if cycle.route_binding_sha256 is None:
            if self._collector.snapshot().phase is not RuntimeObservationPhase.WAITING_BROWSER:
                self._reject("runtime_route_started_late")
                return RuntimeIngressStatus.REJECTED
            cycle.route_binding_sha256 = binding
            cycle.session_binding_sha256 = session_binding_sha256
            return RuntimeIngressStatus.ACCEPTED
        if (
            cycle.route_binding_sha256 == binding
            and cycle.session_binding_sha256 == session_binding_sha256
        ):
            return RuntimeIngressStatus.ACCEPTED
        self._reject("runtime_route_changed")
        return RuntimeIngressStatus.REJECTED

    def commit_capability(
        self,
        cycle: ActiveRuntimeCycle,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
        inventory_sha256: str,
        tool_count: int,
        terminal_execute_present: bool,
        terminal_interact_present: bool,
        now: datetime,
    ) -> CapabilityIngressResult:
        if not self._thread_matches(cycle, thread_id):
            return CapabilityIngressResult(RuntimeIngressStatus.NOT_APPLICABLE)
        self._require_route(
            cycle,
            thread_id=thread_id,
            computer_session_id=computer_session_id,
            session_binding_sha256=session_binding_sha256,
        )
        snapshot = self._collector.snapshot()
        if (
            snapshot.phase is not RuntimeObservationPhase.WAITING_BROWSER
            or snapshot.last_recorded_at is None
        ):
            raise self._failure("runtime is not waiting for capability evidence")
        if (
            inventory_sha256 != cycle.release.inventory_sha256
            or tool_count != 47
            or not terminal_execute_present
            or not terminal_interact_present
        ):
            raise self._failure("runtime capability inventory is incomplete")
        proof = read_latest_browser_evidence(
            self._evidence_dir,
            expected_session_binding_sha256=cycle.codex_session_binding_sha256,
            not_before=snapshot.last_recorded_at,
            now=now,
        )
        if now.tzinfo is None or now <= proof.recorded_at:
            raise self._failure("runtime capability time is not increasing")
        browser = self._authority.browser(
            cycle.release,
            evidence_sha256=proof.evidence_sha256,
            recorded_at=proof.recorded_at,
        )
        exposure = self._authority.tool_exposure(
            cycle.release,
            evidence_sha256=inventory_sha256,
            recorded_at=now,
            session_binding_sha256=session_binding_sha256,
        )
        browser_result = self._collector.observe_browser(browser)
        if browser_result.phase is not RuntimeObservationPhase.WAITING_TOOL_EXPOSURE:
            raise self._failure("Browser runtime observation was rejected")
        exposure_result = self._collector.observe_tool_exposure(exposure)
        if exposure_result.phase is not RuntimeObservationPhase.WAITING_TERMINAL:
            raise self._failure("tool exposure runtime observation was rejected")
        return CapabilityIngressResult(
            RuntimeIngressStatus.ACCEPTED,
            cycle.cycle_binding_sha256,
        )

    def observe_terminal(
        self,
        cycle: ActiveRuntimeCycle,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
        cycle_binding_sha256: str | None,
        evidence: TerminalIngressEvidence,
    ) -> RuntimeIngressStatus:
        if not self._thread_matches(cycle, thread_id):
            return RuntimeIngressStatus.NOT_APPLICABLE
        try:
            self._require_route(
                cycle,
                thread_id=thread_id,
                computer_session_id=computer_session_id,
                session_binding_sha256=session_binding_sha256,
            )
            if cycle_binding_sha256 != cycle.cycle_binding_sha256:
                return RuntimeIngressStatus.NOT_APPLICABLE
            if (
                evidence.expected_observation_id is not None
                and evidence.observation_id != evidence.expected_observation_id
            ):
                raise self._failure("terminal observation binding changed")
            observation_sha256 = terminal_observation_sha256(evidence.observation_id)
            action = cast(
                TerminalToolAction,
                evidence.tool_name.removeprefix("terminal_window_"),
            )
            observation = self._authority.terminal(
                cycle.release,
                evidence_sha256=terminal_runtime_evidence_sha256(
                    tool_name=evidence.tool_name,
                    observation_sha256=observation_sha256,
                    identity_digest=evidence.identity_digest,
                    recorded_at=evidence.recorded_at,
                ),
                recorded_at=evidence.recorded_at,
                session_binding_sha256=session_binding_sha256,
                tool_name=evidence.tool_name,
                action=action,
                observation_bound=action != "capture",
                observation_sha256=observation_sha256,
            )
            result = self._collector.observe_terminal(observation)
            if result.phase is RuntimeObservationPhase.INVALID:
                return RuntimeIngressStatus.REJECTED
            return RuntimeIngressStatus.ACCEPTED
        except (RuntimeIngressError, ValueError):
            self._reject("terminal_runtime_provenance_invalid")
            return RuntimeIngressStatus.REJECTED

    def _require_route(
        self,
        cycle: ActiveRuntimeCycle,
        *,
        thread_id: str,
        computer_session_id: str,
        session_binding_sha256: str,
    ) -> None:
        expected = runtime_route_binding_sha256(
            thread_id,
            computer_session_id,
            session_binding_sha256,
        )
        if (
            cycle.route_binding_sha256 != expected
            or cycle.session_binding_sha256 != session_binding_sha256
        ):
            raise self._failure("runtime route binding changed")

    @staticmethod
    def _thread_matches(cycle: ActiveRuntimeCycle, thread_id: str) -> bool:
        return (
            browser_session_binding_sha256(thread_id)
            == cycle.codex_session_binding_sha256
        )

    def _failure(self, message: str) -> RuntimeIngressError:
        self._reject("runtime_provenance_invalid")
        return RuntimeIngressError(message)

    def _reject(self, failure_code: str) -> None:
        _ = self._collector.invalidate(failure_code)


__all__ = [
    "ActiveRuntimeCycle",
    "CapabilityIngressResult",
    "RuntimeIngressError",
    "RuntimeIngressStatus",
    "RuntimeObservationIngress",
    "TerminalIngressEvidence",
]
