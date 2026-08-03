from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from codex_pro_runtime_receipt_models import RuntimeReceiptSet
from codex_pro_runtime_receipts import (
    NOT_APPLICABLE_CHECK_IDS,
    RUNTIME_CHECK_IDS,
    RuntimeReceiptEvaluation,
    evaluate_runtime_receipts,
)


SCHEMA_VERSION = 2
REQUIRED_CHECK_IDS = (
    "repository_revision_stable",
    "plugin_manifest",
    "remote_mcp_capability_contract",
    "runtime_receipt_contract",
    "host_installer_inventory_contract",
    "browser_evidence_contract",
    "pro_runtime_contract",
    "installed_plugin_inventory",
    "fresh_resident_preflight",
)
DEFERRED_CHECK_IDS = RUNTIME_CHECK_IDS


class EvidenceStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    STALE = "stale"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class EvidenceCheck:
    check_id: str
    category: str
    status: EvidenceStatus


@dataclass(frozen=True, slots=True)
class ProReleaseEvidence:
    repository_revision: str
    workspace_state: str
    host_platform: str
    plugin_version: str
    checks: tuple[EvidenceCheck, ...]

    @property
    def pre_restart_ready(self) -> bool:
        return all(check.status == EvidenceStatus.PASSED for check in self.checks)

    def release_readiness(
        self,
        runtime_receipts: RuntimeReceiptSet | None = None,
        *,
        evaluated_at: datetime | None = None,
    ) -> RuntimeReceiptEvaluation:
        return evaluate_runtime_receipts(
            runtime_receipts,
            repository_revision=self.repository_revision,
            plugin_version=self.plugin_version,
            pre_restart_ready=self.pre_restart_ready,
            evaluated_at=evaluated_at,
        )

    def to_payload(
        self,
        runtime_receipts: RuntimeReceiptSet | None = None,
        *,
        evaluated_at: datetime | None = None,
    ) -> dict[str, object]:
        readiness = self.release_readiness(
            runtime_receipts,
            evaluated_at=evaluated_at,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "certification_scope": (
                "release" if runtime_receipts is not None else "pre_restart"
            ),
            "repository_revision": self.repository_revision,
            "workspace_state": self.workspace_state,
            "host_platform": self.host_platform,
            "plugin_version": self.plugin_version,
            "required_check_ids": list(REQUIRED_CHECK_IDS),
            "checks": [
                {
                    "id": check.check_id,
                    "category": check.category,
                    "status": check.status.value,
                }
                for check in self.checks
            ],
            "pre_restart_ready": self.pre_restart_ready,
            "release_ready": readiness.ready,
            "release_blockers": list(readiness.blockers),
            "runtime_evidence": readiness.to_payload(),
            "deferred_check_ids": list(readiness.missing_check_ids),
            "not_applicable_check_ids": list(NOT_APPLICABLE_CHECK_IDS),
        }

    def summary(
        self,
        runtime_receipts: RuntimeReceiptSet | None = None,
        *,
        evaluated_at: datetime | None = None,
    ) -> str:
        readiness = self.release_readiness(
            runtime_receipts,
            evaluated_at=evaluated_at,
        )
        headline = "PRE-RESTART READY" if self.pre_restart_ready else "PRE-RESTART BLOCKED"
        lines = [headline]
        lines.extend(
            f"{check.status.value.upper():9} {check.check_id}"
            for check in self.checks
        )
        if readiness.missing_check_ids:
            lines.append("DEFERRED  " + ", ".join(readiness.missing_check_ids))
        lines.append(
            "RELEASE READY: " + ("YES" if readiness.ready else "NO")
        )
        if readiness.blockers:
            lines.append("BLOCKERS  " + ", ".join(readiness.blockers))
        return "\n".join(lines) + "\n"


def normalize_checks(checks: tuple[EvidenceCheck, ...]) -> tuple[EvidenceCheck, ...]:
    by_id: dict[str, list[EvidenceCheck]] = {}
    for check in checks:
        by_id.setdefault(check.check_id, []).append(check)
    normalized: list[EvidenceCheck] = []
    for check_id in REQUIRED_CHECK_IDS:
        matches = by_id.get(check_id, [])
        if not matches:
            normalized.append(EvidenceCheck(check_id, "contract", EvidenceStatus.SKIPPED))
        elif len(matches) != 1:
            normalized.append(EvidenceCheck(check_id, "contract", EvidenceStatus.MALFORMED))
        else:
            normalized.append(matches[0])
    if set(by_id).difference(REQUIRED_CHECK_IDS):
        normalized[0] = EvidenceCheck(
            REQUIRED_CHECK_IDS[0], "contract", EvidenceStatus.MALFORMED
        )
    return tuple(normalized)


def write_evidence_artifacts(
    evidence: ProReleaseEvidence,
    json_path: Path,
    runtime_receipts: RuntimeReceiptSet | None = None,
    *,
    evaluated_at: datetime | None = None,
) -> tuple[Path, Path]:
    summary_path = json_path.with_suffix(".txt")
    payload = json.dumps(
        evidence.to_payload(runtime_receipts, evaluated_at=evaluated_at),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    _atomic_write(json_path, payload)
    _atomic_write(
        summary_path,
        evidence.summary(runtime_receipts, evaluated_at=evaluated_at),
    )
    return json_path, summary_path


def read_evidence(path: Path) -> dict[str, object]:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise ValueError("release evidence must be a JSON object")
    return cast("dict[str, object]", value)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(raw_temp_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            _ = target.write(content)
            target.flush()
            os.fsync(target.fileno())
        _ = temp_path.replace(path)
    finally:
        _ = temp_path.unlink(missing_ok=True)
