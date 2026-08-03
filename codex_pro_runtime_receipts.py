from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from codex_pro_runtime_receipt_builders import capability_inventory_sha256
from codex_pro_resident_identity import ResidentRuntimeIdentity
from codex_pro_runtime_receipt_models import (
    ChatGptToolExposureReceipt,
    InAppBrowserReceipt,
    PostRestartRuntimeReceipt,
    REQUIRED_TERMINAL_TOOL_CALL_COUNTS,
    REQUIRED_TERMINAL_TOOL_CALLS,
    RuntimeReceiptSet,
    TerminalToolCallReceipt,
)
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    EXPECTED_TOOL_NAMES,
    build_capability_inventory,
)

RUNTIME_CHECK_IDS = (
    "in_app_browser_live_evidence",
    "chatgpt_tool_exposure",
    "chatgpt_terminal_interaction",
    "post_restart_runtime",
)
NOT_APPLICABLE_CHECK_IDS = ("other_platform_installer_contract",)
_MAX_RECEIPT_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_TERMINAL_INTERACTION_SEQUENCE = (
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_capture",
    "terminal_window_keys",
    "terminal_window_capture",
    "terminal_window_interrupt",
)


@dataclass(frozen=True, slots=True)
class RuntimeReceiptEvaluation:
    ready: bool
    blockers: tuple[str, ...]
    satisfied_check_ids: tuple[str, ...]
    missing_check_ids: tuple[str, ...]
    receipt_set_sha256: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "satisfied_check_ids": list(self.satisfied_check_ids),
            "missing_check_ids": list(self.missing_check_ids),
            "receipt_set_sha256": self.receipt_set_sha256,
        }


def evaluate_runtime_receipts(
    receipts: RuntimeReceiptSet | None,
    *,
    repository_revision: str,
    plugin_version: str,
    pre_restart_ready: bool,
    current_resident: ResidentRuntimeIdentity | None = None,
    evaluated_at: datetime | None = None,
) -> RuntimeReceiptEvaluation:
    if receipts is None:
        missing_blockers = ("runtime_receipts_missing",)
        if not pre_restart_ready:
            missing_blockers = (
                "pre_restart_evidence_not_ready",
                *missing_blockers,
            )
        return RuntimeReceiptEvaluation(
            ready=False,
            blockers=missing_blockers,
            satisfied_check_ids=(),
            missing_check_ids=RUNTIME_CHECK_IDS,
            receipt_set_sha256=None,
        )
    now = evaluated_at or datetime.now(UTC)
    blockers: list[str] = []
    if not pre_restart_ready:
        blockers.append("pre_restart_evidence_not_ready")
    expected_inventory = capability_inventory_sha256(
        build_capability_inventory(EXPECTED_TOOL_NAMES)
    )
    for receipt in receipts.receipts:
        if receipt.repository_revision != repository_revision:
            blockers.append("repository_revision_mismatch")
        if receipt.plugin_version != plugin_version:
            blockers.append("plugin_version_mismatch")
        if receipt.inventory_sha256 != expected_inventory:
            blockers.append("capability_inventory_mismatch")
        if receipt.recorded_at < now - _MAX_RECEIPT_AGE:
            blockers.append("runtime_receipt_stale")
        if receipt.recorded_at > now + _MAX_FUTURE_SKEW:
            blockers.append("runtime_receipt_from_future")
    evidence_hashes = tuple(receipt.evidence_sha256 for receipt in receipts.receipts)
    if len(set(evidence_hashes)) != len(evidence_hashes):
        blockers.append("duplicate_runtime_evidence")

    browsers = tuple(
        receipt
        for receipt in receipts.receipts
        if isinstance(receipt, InAppBrowserReceipt)
    )
    exposures = tuple(
        receipt
        for receipt in receipts.receipts
        if isinstance(receipt, ChatGptToolExposureReceipt)
    )
    restarts = tuple(
        receipt
        for receipt in receipts.receipts
        if isinstance(receipt, PostRestartRuntimeReceipt)
    )
    calls = tuple(
        receipt
        for receipt in receipts.receipts
        if isinstance(receipt, TerminalToolCallReceipt)
    )
    _require_exactly_one(blockers, "in_app_browser_receipt", len(browsers))
    _require_exactly_one(blockers, "tool_exposure_receipt", len(exposures))
    _require_exactly_one(blockers, "post_restart_receipt", len(restarts))
    for tool_name, expected_count in REQUIRED_TERMINAL_TOOL_CALL_COUNTS.items():
        count = sum(receipt.tool_name == tool_name for receipt in calls)
        if count != expected_count:
            blockers.append(f"{tool_name}_count_invalid")
    if len(calls) != sum(REQUIRED_TERMINAL_TOOL_CALL_COUNTS.values()):
        blockers.append("unexpected_terminal_tool_call_receipt")
    _validate_observation_chain(blockers, calls)
    if len(restarts) == 1:
        restart = restarts[0]
        _validate_current_resident(
            blockers,
            restart,
            current_resident,
            plugin_version=plugin_version,
        )
        later_receipts = (*browsers, *exposures, *calls)
        if any(receipt.recorded_at <= restart.recorded_at for receipt in later_receipts):
            blockers.append("live_evidence_predates_restart")

    normalized_blockers = tuple(dict.fromkeys(blockers))
    satisfied = _satisfied_checks(browsers, exposures, calls, restarts)
    missing = tuple(check for check in RUNTIME_CHECK_IDS if check not in satisfied)
    return RuntimeReceiptEvaluation(
        ready=not normalized_blockers and not missing,
        blockers=normalized_blockers,
        satisfied_check_ids=satisfied,
        missing_check_ids=missing,
        receipt_set_sha256=_receipt_set_sha256(receipts),
    )


def _validate_current_resident(
    blockers: list[str],
    restart: PostRestartRuntimeReceipt,
    current: ResidentRuntimeIdentity | None,
    *,
    plugin_version: str,
) -> None:
    if current is None:
        blockers.append("resident_identity_missing")
        return
    if current.resident_generation != restart.resident_generation:
        blockers.append("resident_generation_mismatch")
    if current.resident_started_at != restart.resident_started_at:
        blockers.append("resident_started_at_mismatch")
    if (
        current.plugin_fingerprint_sha256
        != restart.plugin_fingerprint_sha256
    ):
        blockers.append("resident_plugin_fingerprint_mismatch")
    if current.browser_plugin_version != restart.browser_plugin_version:
        blockers.append("browser_plugin_version_mismatch")
    if current.remote_plugin_version != plugin_version:
        blockers.append("resident_plugin_version_mismatch")
    if current.protocol_version != restart.protocol_version:
        blockers.append("resident_protocol_version_mismatch")


def _require_exactly_one(blockers: list[str], label: str, count: int) -> None:
    if count != 1:
        blockers.append(f"{label}_count_invalid")


def _satisfied_checks(
    browsers: tuple[InAppBrowserReceipt, ...],
    exposures: tuple[ChatGptToolExposureReceipt, ...],
    calls: tuple[TerminalToolCallReceipt, ...],
    restarts: tuple[PostRestartRuntimeReceipt, ...],
) -> tuple[str, ...]:
    satisfied: list[str] = []
    if len(browsers) == 1:
        satisfied.append(RUNTIME_CHECK_IDS[0])
    if len(exposures) == 1:
        satisfied.append(RUNTIME_CHECK_IDS[1])
    actual_counts = {
        tool_name: sum(receipt.tool_name == tool_name for receipt in calls)
        for tool_name in REQUIRED_TERMINAL_TOOL_CALLS
    }
    if actual_counts == REQUIRED_TERMINAL_TOOL_CALL_COUNTS:
        satisfied.append(RUNTIME_CHECK_IDS[2])
    if len(restarts) == 1:
        satisfied.append(RUNTIME_CHECK_IDS[3])
    return tuple(satisfied)


def _validate_observation_chain(
    blockers: list[str],
    calls: tuple[TerminalToolCallReceipt, ...],
) -> None:
    if tuple(receipt.tool_name for receipt in calls) != _TERMINAL_INTERACTION_SEQUENCE:
        blockers.append("terminal_interaction_sequence_invalid")
    captures = tuple(
        receipt
        for receipt in calls
        if receipt.tool_name == "terminal_window_capture"
    )
    actions = tuple(
        receipt
        for receipt in calls
        if receipt.tool_name != "terminal_window_capture"
    )
    capture_by_observation = {
        receipt.observation_sha256: receipt for receipt in captures
    }
    if len(capture_by_observation) != len(captures):
        blockers.append("duplicate_capture_observation")
    action_observations = tuple(receipt.observation_sha256 for receipt in actions)
    if len(set(action_observations)) != len(action_observations):
        blockers.append("reused_terminal_observation")
    if set(action_observations) != set(capture_by_observation):
        blockers.append("terminal_observation_chain_incomplete")
        return
    if any(
        capture_by_observation[action.observation_sha256].recorded_at
        >= action.recorded_at
        for action in actions
    ):
        blockers.append("terminal_action_predates_capture")
    if len(calls) != len(_TERMINAL_INTERACTION_SEQUENCE):
        return
    for capture, action in zip(calls[::2], calls[1::2], strict=True):
        if (
            capture.tool_name != "terminal_window_capture"
            or action.tool_name == "terminal_window_capture"
            or capture.observation_sha256 != action.observation_sha256
        ):
            blockers.append("terminal_observation_sequence_invalid")
            break


def _receipt_set_sha256(receipts: RuntimeReceiptSet) -> str:
    canonical = json.dumps(
        receipts.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "NOT_APPLICABLE_CHECK_IDS",
    "RUNTIME_CHECK_IDS",
    "RuntimeReceiptEvaluation",
    "evaluate_runtime_receipts",
]
