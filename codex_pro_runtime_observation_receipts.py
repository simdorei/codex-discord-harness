from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from codex_pro_runtime_observation_models import (
    BrowserObservation,
    PostRestartObservation,
    RuntimeObservation,
    TerminalObservation,
    ToolExposureObservation,
)
from codex_pro_runtime_receipt_builders import RuntimeReceiptBuildError
from codex_pro_runtime_receipt_models import (
    ChatGptToolExposureReceipt,
    InAppBrowserReceipt,
    PostRestartRuntimeReceipt,
    RuntimeEvidenceReceipt,
    RuntimeReceiptSet,
    TerminalToolCallReceipt,
    runtime_receipt_id,
)
from codex_pro_runtime_receipts import evaluate_runtime_receipts
from codex_pro_resident_identity import ResidentRuntimeIdentity

_TERMINAL_SEQUENCE = (
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_capture",
    "terminal_window_keys",
    "terminal_window_capture",
    "terminal_window_interrupt",
)


def build_observed_runtime_receipts(
    observations: Sequence[RuntimeObservation],
    *,
    current_resident: ResidentRuntimeIdentity,
) -> RuntimeReceiptSet:
    """Convert one complete, process-verified observation cycle into receipts."""

    if len(observations) != 9:
        raise RuntimeReceiptBuildError(
            "runtime observation cycle is not complete"
        )
    restart, browser, exposure, *terminal_values = observations
    if (
        not isinstance(restart, PostRestartObservation)
        or not isinstance(browser, BrowserObservation)
        or not isinstance(exposure, ToolExposureObservation)
        or not all(
            isinstance(value, TerminalObservation) for value in terminal_values
        )
    ):
        raise RuntimeReceiptBuildError(
            "runtime observation cycle has an invalid shape"
        )
    terminal = tuple(cast(TerminalObservation, value) for value in terminal_values)
    if tuple(value.tool_name for value in terminal) != _TERMINAL_SEQUENCE:
        raise RuntimeReceiptBuildError(
            "runtime terminal observations are out of order"
        )
    release = restart.release
    if any(value.release != release for value in observations):
        raise RuntimeReceiptBuildError(
            "runtime observation release context changed"
        )
    receipts: list[RuntimeEvidenceReceipt] = [
        _restart_receipt(restart),
        InAppBrowserReceipt.model_validate(
            _common(browser, "in_app_browser")
        ),
        ChatGptToolExposureReceipt.model_validate(
            _common(exposure, "chatgpt_tool_exposure")
        ),
    ]
    receipts.extend(_terminal_receipt(value) for value in terminal)
    receipt_set = RuntimeReceiptSet(receipts=tuple(receipts))
    evaluation = evaluate_runtime_receipts(
        receipt_set,
        repository_revision=release.repository_revision,
        plugin_version=release.plugin_version,
        pre_restart_ready=True,
        current_resident=current_resident,
        evaluated_at=terminal[-1].recorded_at,
    )
    if not evaluation.ready:
        raise RuntimeReceiptBuildError(
            "runtime observation receipts failed release evaluation"
        )
    return receipt_set


def _restart_receipt(
    observation: PostRestartObservation,
) -> PostRestartRuntimeReceipt:
    bindings = (
        str(observation.resident_generation),
        observation.resident_started_at.isoformat(),
        observation.plugin_fingerprint_sha256,
        observation.browser_plugin_version,
        "47",
        "True",
    )
    return PostRestartRuntimeReceipt.model_validate(
        {
            **_common(observation, "post_restart_runtime", bindings),
            "resident_generation": observation.resident_generation,
            "resident_started_at": observation.resident_started_at,
            "plugin_fingerprint_sha256": observation.plugin_fingerprint_sha256,
            "browser_plugin_version": observation.browser_plugin_version,
        }
    )


def _terminal_receipt(
    observation: TerminalObservation,
) -> TerminalToolCallReceipt:
    bindings = (
        observation.action,
        str(observation.observation_bound),
        observation.observation_sha256,
    )
    return TerminalToolCallReceipt.model_validate(
        {
            **_common(observation, observation.tool_name, bindings),
            "tool_name": observation.tool_name,
            "action": observation.action,
            "observation_bound": observation.observation_bound,
            "observation_sha256": observation.observation_sha256,
        }
    )


def _common(
    observation: RuntimeObservation,
    discriminator: str,
    bindings: tuple[str, ...] = (),
) -> dict[str, object]:
    release = observation.release
    return {
        "receipt_id": runtime_receipt_id(
            discriminator,
            observation.evidence_sha256,
            observation.recorded_at,
            repository_revision=release.repository_revision,
            plugin_version=release.plugin_version,
            protocol_version=release.protocol_version,
            inventory_sha256=release.inventory_sha256,
            bindings=bindings,
        ),
        "repository_revision": release.repository_revision,
        "plugin_version": release.plugin_version,
        "protocol_version": release.protocol_version,
        "inventory_sha256": release.inventory_sha256,
        "evidence_sha256": observation.evidence_sha256,
        "recorded_at": observation.recorded_at,
    }


__all__ = ["build_observed_runtime_receipts"]
