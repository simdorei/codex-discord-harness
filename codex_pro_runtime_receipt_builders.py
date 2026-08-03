from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import cast, override

from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_models import (
    ChatGptToolExposureReceipt,
    InAppBrowserReceipt,
    PostRestartRuntimeReceipt,
    RuntimeReceiptContext,
    TerminalToolAction,
    TerminalToolCallReceipt,
    TerminalToolName,
    runtime_receipt_id,
)
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    EXPECTED_TOOL_NAMES,
    CapabilityInventoryOutput,
    CapabilitySurface,
    capability_inventory_sha256 as _capability_inventory_sha256,
)
from simdorei_mcp_common.runtime_provenance import terminal_observation_sha256
from simdorei_mcp_common.terminal_window_interaction_protocol import (
    TerminalWindowActionOutput,
    TerminalWindowCaptureOutput,
)

_BROWSER_EVIDENCE_PROTOCOL = "ask-chatgpt-pro-browser-evidence-v1"


class RuntimeReceiptBuildError(ValueError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "runtime receipt could not be built"


def capability_inventory_sha256(inventory: CapabilityInventoryOutput) -> str:
    return _capability_inventory_sha256(inventory)


def runtime_receipt_context(
    repository_revision: str,
    plugin_version: str,
    inventory: CapabilityInventoryOutput,
    recorded_at: datetime,
) -> RuntimeReceiptContext:
    _require_release_inventory(inventory)
    return RuntimeReceiptContext(
        repository_revision=repository_revision,
        plugin_version=plugin_version,
        inventory_sha256=capability_inventory_sha256(inventory),
        recorded_at=recorded_at,
    )


def browser_receipt(
    context: RuntimeReceiptContext,
    raw_browser_evidence: Mapping[str, object],
) -> InAppBrowserReceipt:
    if (
        raw_browser_evidence.get("protocol") != _BROWSER_EVIDENCE_PROTOCOL
        or raw_browser_evidence.get("browser_type") != "iab"
        or raw_browser_evidence.get("status") != "available"
        or raw_browser_evidence.get("can_report_unavailable") is not False
    ):
        raise RuntimeReceiptBuildError(
            "in-app Browser evidence did not prove an available iab runtime"
        )
    evidence_sha256 = _sha256(dict(raw_browser_evidence))
    return InAppBrowserReceipt.model_validate(
        _common(context, "in_app_browser", evidence_sha256)
    )


def tool_exposure_receipt(
    context: RuntimeReceiptContext,
    inventory: CapabilityInventoryOutput,
) -> ChatGptToolExposureReceipt:
    _require_release_inventory(inventory)
    evidence_sha256 = capability_inventory_sha256(inventory)
    if evidence_sha256 != context.inventory_sha256:
        raise RuntimeReceiptBuildError(
            "ChatGPT tool exposure inventory differs from the receipt context"
        )
    return ChatGptToolExposureReceipt.model_validate(
        _common(context, "chatgpt_tool_exposure", evidence_sha256)
    )


def terminal_tool_call_receipt(
    context: RuntimeReceiptContext,
    tool_name: TerminalToolName,
    output: TerminalWindowCaptureOutput | TerminalWindowActionOutput,
) -> TerminalToolCallReceipt:
    action = cast(TerminalToolAction, tool_name.removeprefix("terminal_window_"))
    if action == "capture":
        if not isinstance(output, TerminalWindowCaptureOutput):
            raise RuntimeReceiptBuildError(
                "terminal capture receipt requires a capture output"
            )
        observation_bound = False
        observation_id = output.observation_id
    else:
        if not isinstance(output, TerminalWindowActionOutput):
            raise RuntimeReceiptBuildError(
                "terminal action receipt requires an action output"
            )
        if output.receipt.action != action:
            raise RuntimeReceiptBuildError(
                "terminal tool output action differs from the requested tool"
            )
        observation_bound = output.receipt.observation_id is not None
        observation_id = output.receipt.observation_id
        if observation_id is None:
            raise RuntimeReceiptBuildError(
                "terminal action output has no observation binding"
            )
    evidence_sha256 = _sha256(output.model_dump(mode="json"))
    observation_sha256 = terminal_observation_sha256(observation_id)
    bindings = (action, str(observation_bound), observation_sha256)
    return TerminalToolCallReceipt.model_validate(
        {
            **_common(context, tool_name, evidence_sha256, bindings),
            "tool_name": tool_name,
            "action": action,
            "observation_bound": observation_bound,
            "observation_sha256": observation_sha256,
        }
    )


def post_restart_receipt(
    context: RuntimeReceiptContext,
    *,
    runtime_status: ProRuntimeStatus,
    resident_started_at: datetime,
    plugin_fingerprint_sha256: str,
) -> PostRestartRuntimeReceipt:
    if runtime_status.remote_plugin_version != context.plugin_version:
        raise RuntimeReceiptBuildError(
            "resident plugin version differs from the receipt context"
        )
    if runtime_status.resident_plugin_fingerprint != plugin_fingerprint_sha256:
        raise RuntimeReceiptBuildError(
            "resident plugin fingerprint differs from the runtime status"
        )
    evidence_sha256 = post_restart_evidence_sha256(
        resident_generation=runtime_status.resident_generation,
        resident_started_at=resident_started_at,
        plugin_fingerprint_sha256=plugin_fingerprint_sha256,
        browser_plugin_version=runtime_status.browser_plugin_version,
    )
    bindings = (
        str(runtime_status.resident_generation),
        resident_started_at.isoformat(),
        plugin_fingerprint_sha256,
        runtime_status.browser_plugin_version,
        "47",
        "True",
    )
    return PostRestartRuntimeReceipt.model_validate(
        {
            **_common(
                context,
                "post_restart_runtime",
                evidence_sha256,
                bindings,
            ),
            "resident_generation": runtime_status.resident_generation,
            "resident_started_at": resident_started_at,
            "plugin_fingerprint_sha256": plugin_fingerprint_sha256,
            "browser_plugin_version": runtime_status.browser_plugin_version,
        }
    )


def post_restart_evidence_sha256(
    *,
    resident_generation: int,
    resident_started_at: datetime,
    plugin_fingerprint_sha256: str,
    browser_plugin_version: str,
) -> str:
    return _sha256(
        {
            "resident_generation": resident_generation,
            "resident_started_at": resident_started_at.isoformat(),
            "plugin_fingerprint_sha256": plugin_fingerprint_sha256,
            "browser_plugin_version": browser_plugin_version,
            "healthy": True,
        }
    )
def _require_release_inventory(inventory: CapabilityInventoryOutput) -> None:
    if (
        not inventory.ready
        or inventory.expected_tool_count != 47
        or inventory.registered_tool_count != 47
        or inventory.missing_tools
        or inventory.unexpected_tools
        or inventory.manifest_duplicate_tools
    ):
        raise RuntimeReceiptBuildError(
            "MCP capability inventory is not the reviewed 47-tool release"
        )
    if len(EXPECTED_TOOL_NAMES) != 47:
        raise RuntimeReceiptBuildError("source capability manifest is not 47 tools")
    groups = {group.surface: group for group in inventory.groups}
    interaction = groups.get(CapabilitySurface.TERMINAL_INTERACT)
    if interaction is None or "terminal:interact" not in interaction.oauth_scopes:
        raise RuntimeReceiptBuildError(
            "MCP capability inventory does not expose terminal:interact"
        )


def _common(
    context: RuntimeReceiptContext,
    discriminator: str,
    evidence_sha256: str,
    bindings: tuple[str, ...] = (),
) -> dict[str, object]:
    receipt_id = runtime_receipt_id(
        discriminator,
        evidence_sha256,
        context.recorded_at,
        repository_revision=context.repository_revision,
        plugin_version=context.plugin_version,
        protocol_version=11,
        inventory_sha256=context.inventory_sha256,
        bindings=bindings,
    )
    return {
        "receipt_id": receipt_id,
        "repository_revision": context.repository_revision,
        "plugin_version": context.plugin_version,
        "inventory_sha256": context.inventory_sha256,
        "evidence_sha256": evidence_sha256,
        "recorded_at": context.recorded_at,
    }


def _sha256(value: object) -> str:
    try:
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeReceiptBuildError(
            "runtime evidence is not canonical JSON"
        ) from exc
    return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = [
    "RuntimeReceiptBuildError",
    "RuntimeReceiptContext",
    "browser_receipt",
    "capability_inventory_sha256",
    "post_restart_receipt",
    "post_restart_evidence_sha256",
    "runtime_receipt_context",
    "terminal_tool_call_receipt",
    "tool_exposure_receipt",
]
