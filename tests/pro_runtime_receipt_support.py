from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codex_pro_release_evidence import (
    REQUIRED_CHECK_IDS,
    EvidenceCheck,
    EvidenceStatus,
    ProReleaseEvidence,
)
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_builders import (
    RuntimeReceiptContext,
    browser_receipt,
    post_restart_receipt,
    runtime_receipt_context,
    terminal_tool_call_receipt,
    tool_exposure_receipt,
)
from codex_pro_runtime_receipt_models import (
    RuntimeEvidenceReceipt,
    RuntimeReceiptSet,
    TerminalToolName,
)
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    EXPECTED_TOOL_NAMES,
    build_capability_inventory,
)
from simdorei_mcp_common.terminal_window_interaction_protocol import (
    TerminalWindowAction,
    TerminalWindowActionOutput,
    TerminalWindowActionReceipt,
    TerminalWindowCaptureOutput,
    TerminalWindowRect,
)
from simdorei_mcp_common.terminal_window_protocol import TerminalWindowEntry

REVISION = "a" * 40
PLUGIN_VERSION = "0.1.0-test"
WINDOW_ID = "termwin_0123456789abcdef"
INVENTORY = build_capability_inventory(EXPECTED_TOOL_NAMES)


def ready_release_evidence() -> ProReleaseEvidence:
    return ProReleaseEvidence(
        repository_revision=REVISION,
        workspace_state="clean",
        host_platform="windows",
        plugin_version=PLUGIN_VERSION,
        checks=tuple(
            EvidenceCheck(check_id, "source", EvidenceStatus.PASSED)
            for check_id in REQUIRED_CHECK_IDS
        ),
    )


def complete_runtime_receipts(
    now: datetime | None = None,
    *,
    raw_browser_evidence: dict[str, object] | None = None,
) -> RuntimeReceiptSet:
    evaluated_at = now or datetime.now(UTC)
    restart_time = evaluated_at - timedelta(minutes=5)
    restart_context = _context(restart_time)
    receipts: list[RuntimeEvidenceReceipt] = [
        post_restart_receipt(
            restart_context,
            runtime_status=ProRuntimeStatus(
                remote_plugin_version=PLUGIN_VERSION,
                browser_plugin_version="26.721.41059",
                resident_generation=7,
            ),
            resident_started_at=restart_time - timedelta(seconds=2),
            plugin_fingerprint_sha256="f" * 64,
        )
    ]
    browser_context = _context(evaluated_at - timedelta(minutes=4))
    receipts.append(
        browser_receipt(
            browser_context,
            raw_browser_evidence
            or {
                "protocol": "ask-chatgpt-pro-browser-evidence-v1",
                "browser_type": "iab",
                "status": "available",
                "can_report_unavailable": False,
            },
        )
    )
    exposure_context = _context(evaluated_at - timedelta(minutes=3))
    receipts.append(tool_exposure_receipt(exposure_context, INVENTORY))
    outputs: tuple[
        tuple[
            TerminalToolName,
            TerminalWindowCaptureOutput | TerminalWindowActionOutput,
        ],
        ...,
    ] = (
        ("terminal_window_capture", _capture_output(1)),
        ("terminal_window_type", _action_output("type", 1)),
        ("terminal_window_capture", _capture_output(2)),
        ("terminal_window_keys", _action_output("keys", 2)),
        ("terminal_window_capture", _capture_output(3)),
        ("terminal_window_interrupt", _action_output("interrupt", 3)),
    )
    for offset, (tool_name, output) in enumerate(outputs):
        context = _context(evaluated_at - timedelta(minutes=2, seconds=-offset))
        receipts.append(terminal_tool_call_receipt(context, tool_name, output))
    return RuntimeReceiptSet(receipts=tuple(receipts))


def _context(recorded_at: datetime) -> RuntimeReceiptContext:
    return runtime_receipt_context(
        REVISION,
        PLUGIN_VERSION,
        INVENTORY,
        recorded_at,
    )


def _entry() -> TerminalWindowEntry:
    return TerminalWindowEntry(
        terminal_window_id=WINDOW_ID,
        window_id=42,
        process_id=84,
        shell="powershell",
        cwd="C:/qa",
        title="Codex Pro Terminal",
    )


def _capture_output(index: int) -> TerminalWindowCaptureOutput:
    return TerminalWindowCaptureOutput(
        window=_entry(),
        observation_id=f"twobs_{index:016x}",
        identity_digest="b" * 64,
        rect=TerminalWindowRect(left=1, top=2, width=640, height=480),
        data_base64="aGVsbG8gd29ybGQ=",
        captured_at=datetime.now(UTC),
    )


def _action_output(
    action: TerminalWindowAction,
    index: int,
) -> TerminalWindowActionOutput:
    keys: tuple[str, ...] = ()
    unicode_chars = 0
    if action == "type":
        unicode_chars = 7
    elif action == "keys":
        keys = ("ENTER",)
    elif action == "interrupt":
        keys = ("CTRL", "C")
    return TerminalWindowActionOutput(
        window=_entry(),
        receipt=TerminalWindowActionReceipt(
            receipt_id=f"twrcpt_{index:016x}",
            terminal_window_id=WINDOW_ID,
            observation_id=f"twobs_{index:016x}",
            identity_digest="b" * 64,
            action=action,
            unicode_chars=unicode_chars,
            keys=keys,
            activated=False,
            completed_at=datetime.now(UTC),
        ),
    )


__all__ = [
    "INVENTORY",
    "PLUGIN_VERSION",
    "REVISION",
    "complete_runtime_receipts",
    "ready_release_evidence",
]
