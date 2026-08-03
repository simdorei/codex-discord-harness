from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codex_pro_runtime_receipt_io import (
    RuntimeReceiptError,
    publish_runtime_receipts,
    read_runtime_receipts,
    write_runtime_receipts,
)
from codex_pro_runtime_receipts import (
    RUNTIME_CHECK_IDS,
)
from tests.pro_runtime_receipt_support import (
    PLUGIN_VERSION,
    REVISION,
    complete_runtime_receipts,
    ready_release_evidence,
)


def test_complete_receipts_derive_release_readiness_and_round_trip() -> None:
    now = datetime.now(UTC)
    receipts = complete_runtime_receipts(now)
    evidence = ready_release_evidence()

    readiness = evidence.release_readiness(receipts, evaluated_at=now)
    payload = evidence.to_payload(receipts, evaluated_at=now)
    with tempfile.TemporaryDirectory() as raw_dir:
        path = Path(raw_dir) / "runtime-receipts.json"
        _ = write_runtime_receipts(receipts, path)
        restored = read_runtime_receipts(path)

    assert readiness.ready is True
    assert readiness.blockers == ()
    assert readiness.satisfied_check_ids == RUNTIME_CHECK_IDS
    assert readiness.missing_check_ids == ()
    assert readiness.receipt_set_sha256 is not None
    assert payload["release_ready"] is True
    assert payload["deferred_check_ids"] == []
    assert restored == receipts


def test_receipt_publication_is_idempotent_and_rejects_cycle_conflicts(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    first = complete_runtime_receipts(now)
    conflicting = complete_runtime_receipts(now + timedelta(minutes=1))
    path = tmp_path / "runtime-receipts.json"

    assert publish_runtime_receipts(first, path) == path
    assert publish_runtime_receipts(first, path) == path
    with pytest.raises(RuntimeReceiptError, match="different observation cycle"):
        _ = publish_runtime_receipts(conflicting, path)

    assert read_runtime_receipts(path) == first


def test_receipts_persist_hashes_without_raw_browser_or_terminal_data() -> None:
    raw_secret = "qa-token-value-must-not-persist"
    private_scope = "codex-project-private-scope-value"
    private_path = "C:/private/customer/terminal.png"
    receipts = complete_runtime_receipts(
        raw_browser_evidence={
            "protocol": "ask-chatgpt-pro-browser-evidence-v1",
            "browser_type": "iab",
            "status": "available",
            "can_report_unavailable": False,
            "token": raw_secret,
            "project_scope": private_scope,
            "screenshot_path": private_path,
        }
    )
    serialized = receipts.model_dump_json()

    for forbidden in (raw_secret, private_scope, private_path, "aGVsbG8gd29ybGQ="):
        assert forbidden not in serialized
    for forbidden_field in (
        '"command"',
        '"text"',
        '"data_base64"',
        '"cwd"',
        '"project_scope"',
        '"conversation_scope"',
        '"token"',
        '"cookie"',
        '"password"',
        '"otp"',
    ):
        assert forbidden_field not in serialized.casefold()


def test_missing_stale_and_cross_revision_receipts_fail_closed() -> None:
    now = datetime.now(UTC)
    evidence = ready_release_evidence()
    missing = evidence.release_readiness(None, evaluated_at=now)
    stale = evidence.release_readiness(
        complete_runtime_receipts(now - timedelta(days=2)),
        evaluated_at=now,
    )
    original = complete_runtime_receipts(now)
    first = original.receipts[0].model_copy(
        update={"repository_revision": "b" * 40}
    )
    drifted = original.model_copy(update={"receipts": (first, *original.receipts[1:])})
    mismatched = evidence.release_readiness(drifted, evaluated_at=now)

    assert missing.ready is False
    assert missing.missing_check_ids == RUNTIME_CHECK_IDS
    assert "runtime_receipts_missing" in missing.blockers
    assert "runtime_receipt_stale" in stale.blockers
    assert "repository_revision_mismatch" in mismatched.blockers


def test_missing_duplicate_and_pre_restart_order_fail_closed() -> None:
    now = datetime.now(UTC)
    evidence = ready_release_evidence()
    complete = complete_runtime_receipts(now)
    without_interrupt = complete.model_copy(
        update={
            "receipts": tuple(
                receipt
                for receipt in complete.receipts
                if getattr(receipt, "tool_name", None)
                != "terminal_window_interrupt"
            )
        }
    )
    duplicated = complete.model_copy(
        update={"receipts": (*complete.receipts, complete.receipts[-1])}
    )
    restart = complete.receipts[0]
    browser = complete.receipts[1].model_copy(
        update={"recorded_at": restart.recorded_at - timedelta(seconds=1)}
    )
    wrong_order = complete.model_copy(
        update={"receipts": (restart, browser, *complete.receipts[2:])}
    )
    type_index = next(
        index
        for index, receipt in enumerate(complete.receipts)
        if getattr(receipt, "tool_name", None) == "terminal_window_type"
    )
    type_receipt = complete.receipts[type_index]
    matching_capture = next(
        receipt
        for receipt in complete.receipts
        if getattr(receipt, "tool_name", None) == "terminal_window_capture"
        and getattr(receipt, "observation_sha256", None)
        == getattr(type_receipt, "observation_sha256", None)
    )
    early_type = type_receipt.model_copy(
        update={"recorded_at": matching_capture.recorded_at - timedelta(seconds=1)}
    )
    reordered_values = list(complete.receipts)
    reordered_values[type_index] = early_type
    action_before_capture = complete.model_copy(
        update={"receipts": tuple(reordered_values)}
    )

    assert "terminal_window_interrupt_count_invalid" in evidence.release_readiness(
        without_interrupt,
        evaluated_at=now,
    ).blockers
    assert "duplicate_runtime_evidence" in evidence.release_readiness(
        duplicated,
        evaluated_at=now,
    ).blockers
    assert "live_evidence_predates_restart" in evidence.release_readiness(
        wrong_order,
        evaluated_at=now,
    ).blockers
    assert "terminal_action_predates_capture" in evidence.release_readiness(
        action_before_capture,
        evaluated_at=now,
    ).blockers


def test_live_evidence_at_restart_timestamp_fails_closed() -> None:
    now = datetime.now(UTC)
    evidence = ready_release_evidence()
    complete = complete_runtime_receipts(now)
    restart = complete.receipts[0]
    browser = complete.receipts[1].model_copy(
        update={"recorded_at": restart.recorded_at}
    )
    same_timestamp = complete.model_copy(
        update={"receipts": (restart, browser, *complete.receipts[2:])}
    )

    assert "live_evidence_predates_restart" in evidence.release_readiness(
        same_timestamp,
        evaluated_at=now,
    ).blockers


def test_terminal_interaction_sequence_and_equal_timestamps_fail_closed() -> None:
    now = datetime.now(UTC)
    evidence = ready_release_evidence()
    complete = complete_runtime_receipts(now)
    prefix = complete.receipts[:3]
    calls = complete.receipts[3:]
    reordered = complete.model_copy(
        update={"receipts": (*prefix, calls[2], calls[3], calls[0], calls[1], *calls[4:])}
    )
    equal_action = calls[1].model_copy(
        update={"recorded_at": calls[0].recorded_at}
    )
    equal_timestamp = complete.model_copy(
        update={"receipts": (*prefix, calls[0], equal_action, *calls[2:])}
    )

    assert "terminal_interaction_sequence_invalid" in evidence.release_readiness(
        reordered,
        evaluated_at=now,
    ).blockers
    assert "terminal_action_predates_capture" in evidence.release_readiness(
        equal_timestamp,
        evaluated_at=now,
    ).blockers


def test_plugin_and_inventory_binding_fail_closed() -> None:
    now = datetime.now(UTC)
    evidence = ready_release_evidence()
    complete = complete_runtime_receipts(now)
    plugin = complete.receipts[0].model_copy(update={"plugin_version": "stale"})
    inventory = complete.receipts[1].model_copy(
        update={"inventory_sha256": "0" * 64}
    )
    drifted = complete.model_copy(
        update={"receipts": (plugin, inventory, *complete.receipts[2:])}
    )
    blockers = evidence.release_readiness(drifted, evaluated_at=now).blockers

    assert "plugin_version_mismatch" in blockers
    assert "capability_inventory_mismatch" in blockers
    assert PLUGIN_VERSION != "stale"
    assert REVISION == evidence.repository_revision


def test_current_resident_identity_is_required_and_exactly_bound() -> None:
    now = datetime.now(UTC)
    receipts = complete_runtime_receipts(now)
    evidence = ready_release_evidence(now)
    resident = evidence.resident_identity
    assert resident is not None
    cases = (
        (None, "resident_identity_missing"),
        (
            resident.model_copy(
                update={"resident_generation": resident.resident_generation + 1}
            ),
            "resident_generation_mismatch",
        ),
        (
            resident.model_copy(
                update={
                    "resident_started_at": resident.resident_started_at
                    + timedelta(seconds=1)
                }
            ),
            "resident_started_at_mismatch",
        ),
        (
            resident.model_copy(
                update={"plugin_fingerprint_sha256": "e" * 64}
            ),
            "resident_plugin_fingerprint_mismatch",
        ),
        (
            resident.model_copy(update={"browser_plugin_version": "drifted"}),
            "browser_plugin_version_mismatch",
        ),
    )

    for current, expected in cases:
        candidate = replace(evidence, resident_identity=current)
        blockers = candidate.release_readiness(
            receipts,
            evaluated_at=now,
        ).blockers
        assert expected in blockers
