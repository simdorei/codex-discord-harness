from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from codex_pro_runtime_receipt_builders import (
    RuntimeReceiptBuildError,
    browser_receipt,
    post_restart_receipt,
    runtime_receipt_context,
)
from codex_pro_runtime_receipt_io import RuntimeReceiptError, read_runtime_receipts
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_models import RuntimeReceiptSet, runtime_receipt_id
from tests.pro_runtime_receipt_support import (
    INVENTORY,
    PLUGIN_VERSION,
    REVISION,
    complete_runtime_receipts,
)


def test_schema_rejects_duplicate_ids_protocol_drift_and_hidden_fields() -> None:
    values = _raw_receipts()
    values.append(dict(values[0]))
    with pytest.raises(ValidationError, match="must be unique"):
        _ = RuntimeReceiptSet.model_validate(
            {"schema_version": 1, "receipts": values}
        )

    values = _raw_receipts()
    values[0]["protocol_version"] = 9
    with pytest.raises(ValidationError):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})

    values = _raw_receipts()
    browser = values[1]
    exposure = values[2]
    evidence_sha256 = browser["evidence_sha256"]
    recorded_at = exposure["recorded_at"]
    assert isinstance(evidence_sha256, str)
    assert isinstance(recorded_at, str)
    exposure["evidence_sha256"] = evidence_sha256
    exposure["receipt_id"] = runtime_receipt_id(
        "chatgpt_tool_exposure",
        evidence_sha256,
        datetime.fromisoformat(recorded_at),
        repository_revision=cast(str, exposure["repository_revision"]),
        plugin_version=cast(str, exposure["plugin_version"]),
        protocol_version=cast(int, exposure["protocol_version"]),
        inventory_sha256=cast(str, exposure["inventory_sha256"]),
    )
    with pytest.raises(ValidationError, match="evidence must be unique"):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})

    values = _raw_receipts()
    values[0]["evidence_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="integrity check failed"):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})

    values = _raw_receipts()
    action = next(
        value
        for value in values
        if value.get("tool_name") == "terminal_window_type"
    )
    action["observation_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="integrity check failed"):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})

    values = _raw_receipts()
    values[0]["password"] = "must-not-be-accepted"
    with pytest.raises(ValidationError, match="Extra inputs"):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})

    values = _raw_receipts()
    exposure = next(
        value
        for value in values
        if value.get("receipt_type") == "chatgpt_tool_exposure"
    )
    exposure["tool_count"] = 46
    exposure["terminal_interact_present"] = False
    with pytest.raises(ValidationError):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("repository_revision", "b" * 40),
        ("plugin_version", "tampered-plugin"),
        ("inventory_sha256", "0" * 64),
    ),
)
def test_schema_binds_receipt_id_to_release_context(
    field: str,
    value: str,
) -> None:
    values = _raw_receipts()
    values[0][field] = value

    with pytest.raises(ValidationError, match="integrity check failed"):
        _ = RuntimeReceiptSet.model_validate({"schema_version": 1, "receipts": values})


def test_browser_builder_rejects_unavailable_or_unverified_evidence() -> None:
    context = runtime_receipt_context(
        REVISION,
        PLUGIN_VERSION,
        INVENTORY,
        datetime.now(UTC),
    )

    with pytest.raises(RuntimeReceiptBuildError, match="available iab"):
        _ = browser_receipt(
            context,
            {
                "protocol": "ask-chatgpt-pro-browser-evidence-v1",
                "browser_type": "iab",
                "status": "unavailable",
                "can_report_unavailable": True,
            },
        )


def test_restart_builder_rejects_a_stale_installed_plugin_version() -> None:
    now = datetime.now(UTC)
    context = runtime_receipt_context(
        REVISION,
        PLUGIN_VERSION,
        INVENTORY,
        now,
    )

    with pytest.raises(RuntimeReceiptBuildError, match="plugin version"):
        _ = post_restart_receipt(
            context,
            runtime_status=ProRuntimeStatus(
                remote_plugin_version="stale-plugin",
                browser_plugin_version="browser-version",
                resident_generation=2,
            ),
            resident_started_at=now,
            plugin_fingerprint_sha256="f" * 64,
        )


def test_receipt_reader_surfaces_invalid_utf8_as_malformed(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.json"
    _ = path.write_bytes(b"\xff\xfe")

    with pytest.raises(
        RuntimeReceiptError, match="unavailable or malformed"
    ) as failure:
        _ = read_runtime_receipts(path)
    assert isinstance(failure.value.__cause__, UnicodeDecodeError)


def _raw_receipts() -> list[dict[str, object]]:
    raw = cast(object, json.loads(complete_runtime_receipts().model_dump_json()))
    assert isinstance(raw, dict)
    receipts = cast("dict[str, object]", raw).get("receipts")
    assert isinstance(receipts, list)
    typed: list[dict[str, object]] = []
    for receipt in cast("list[object]", receipts):
        assert isinstance(receipt, dict)
        typed.append(cast("dict[str, object]", receipt))
    return typed
