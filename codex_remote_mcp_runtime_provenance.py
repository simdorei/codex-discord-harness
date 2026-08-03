from __future__ import annotations

from typing import Literal, Protocol, cast, final

from codex_pro_runtime_observation_ingress import (
    RuntimeIngressStatus,
    TerminalIngressEvidence,
)
from codex_pro_runtime_observation_runtime import (
    RuntimeObservationStartError,
    bind_pro_runtime_observation_route,
    commit_pro_runtime_capability,
    invalidate_pro_runtime_observation,
    observe_pro_runtime_terminal,
)
from simdorei_mcp_common.messages import (
    ProjectOperationCommand,
    ProjectSessionCommand,
    RuntimeCapabilityCommand,
    RuntimeCapabilityResult,
)
from simdorei_mcp_common.operation_outputs import ProjectOperationOutput
from simdorei_mcp_common.runtime_provenance import ObservedTerminalTool
from simdorei_mcp_common.terminal_window_interaction_protocol import (
    TerminalWindowActionOutput,
    TerminalWindowCaptureOutput,
    TerminalWindowCaptureRequest,
    TerminalWindowInterruptRequest,
    TerminalWindowKeysRequest,
    TerminalWindowTypeRequest,
)


class RuntimeProvenanceObserver(Protocol):
    def bind_route(self, command: ProjectSessionCommand) -> None: ...

    def capability(
        self, command: RuntimeCapabilityCommand
    ) -> RuntimeCapabilityResult: ...

    def terminal(
        self,
        command: ProjectOperationCommand,
        output: ProjectOperationOutput,
    ) -> None: ...

    def invalidate(self, failure_code: str) -> None: ...


@final
class LocalRuntimeProvenanceObserver:
    def invalidate(self, failure_code: str) -> None:
        invalidate_pro_runtime_observation(failure_code)

    def bind_route(self, command: ProjectSessionCommand) -> None:
        provenance = command.runtime_provenance
        computer_session_id = command.computer_session_id
        if provenance is None or computer_session_id is None:
            return
        _ = bind_pro_runtime_observation_route(
            thread_id=command.thread_id,
            computer_session_id=computer_session_id,
            session_binding_sha256=provenance.session_binding_sha256,
        )

    def capability(
        self, command: RuntimeCapabilityCommand
    ) -> RuntimeCapabilityResult:
        provenance = command.runtime_provenance
        computer_session_id = command.computer_session_id
        if provenance is None or computer_session_id is None:
            raise RuntimeObservationStartError(
                "runtime capability route identity is unavailable"
            )
        result = commit_pro_runtime_capability(
            thread_id=command.thread_id,
            computer_session_id=computer_session_id,
            session_binding_sha256=provenance.session_binding_sha256,
            inventory_sha256=command.inventory_sha256,
            tool_count=command.tool_count,
            terminal_execute_present=command.terminal_execute_present,
            terminal_interact_present=command.terminal_interact_present,
        )
        if result.status is RuntimeIngressStatus.REJECTED:
            raise RuntimeObservationStartError(
                "runtime capability observation was rejected"
            )
        status: Literal["accepted", "not_applicable"] = (
            "accepted"
            if result.status is RuntimeIngressStatus.ACCEPTED
            else "not_applicable"
        )
        return RuntimeCapabilityResult(
            request_id=command.request_id,
            status=status,
            cycle_binding_sha256=result.cycle_binding_sha256,
        )

    def terminal(
        self,
        command: ProjectOperationCommand,
        output: ProjectOperationOutput,
    ) -> None:
        if not _is_observed_terminal_request(command):
            return
        evidence = _terminal_evidence(command, output)
        if evidence is None:
            invalidate_pro_runtime_observation("terminal_runtime_output_invalid")
            return
        provenance = command.runtime_provenance
        computer_session_id = command.computer_session_id
        if provenance is None or computer_session_id is None:
            invalidate_pro_runtime_observation("terminal_runtime_provenance_missing")
            return
        _ = observe_pro_runtime_terminal(
            thread_id=command.thread_id,
            computer_session_id=computer_session_id,
            session_binding_sha256=provenance.session_binding_sha256,
            cycle_binding_sha256=provenance.cycle_binding_sha256,
            evidence=evidence,
        )


def _is_observed_terminal_request(command: ProjectOperationCommand) -> bool:
    return isinstance(
        command.operation,
        (
            TerminalWindowCaptureRequest,
            TerminalWindowTypeRequest,
            TerminalWindowKeysRequest,
            TerminalWindowInterruptRequest,
        ),
    )


def _terminal_evidence(
    command: ProjectOperationCommand,
    output: ProjectOperationOutput,
) -> TerminalIngressEvidence | None:
    operation = command.operation
    match operation:
        case TerminalWindowCaptureRequest():
            if not isinstance(output, TerminalWindowCaptureOutput):
                return None
            return TerminalIngressEvidence(
                tool_name="terminal_window_capture",
                observation_id=output.observation_id,
                identity_digest=output.identity_digest,
                recorded_at=output.captured_at,
            )
        case (
            TerminalWindowTypeRequest()
            | TerminalWindowKeysRequest()
            | TerminalWindowInterruptRequest()
        ):
            if not isinstance(output, TerminalWindowActionOutput):
                return None
            tool_name = cast(ObservedTerminalTool, operation.kind)
            observation_id = output.receipt.observation_id
            if observation_id is None:
                return None
            return TerminalIngressEvidence(
                tool_name=tool_name,
                observation_id=observation_id,
                expected_observation_id=operation.observation_id,
                identity_digest=output.receipt.identity_digest,
                recorded_at=output.receipt.completed_at,
            )
        case _:
            return None


DEFAULT_RUNTIME_PROVENANCE_OBSERVER = LocalRuntimeProvenanceObserver()

__all__ = [
    "DEFAULT_RUNTIME_PROVENANCE_OBSERVER",
    "LocalRuntimeProvenanceObserver",
    "RuntimeProvenanceObserver",
]
