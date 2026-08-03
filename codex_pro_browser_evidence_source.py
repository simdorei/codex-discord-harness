from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Literal, Self, cast, override

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    ValidationError,
    model_validator,
)

from codex_pro_runtime_receipt_models import Sha256Digest

BROWSER_EVIDENCE_PROTOCOL = "ask-chatgpt-pro-browser-evidence-v1"
EXPECTED_BROWSER_PROBE_SHA256 = (
    "ea89b1a6e27dd2a23d53c6925a4683a95f0946b43fd934690a681616fb1a40a4"
)
_MAX_SOURCE_FILES = 1_024
_MAX_SOURCE_AGE = timedelta(minutes=15)
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_MAX_MTIME_SKEW = timedelta(minutes=1)


class BrowserEvidenceSourceError(ValueError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "Browser evidence source is invalid"


class BrowserEvidenceSource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    protocol: Literal["ask-chatgpt-pro-browser-evidence-v1"]
    browser_type: Literal["iab"]
    file_binding_sha256: Sha256Digest
    session_binding_sha256: Sha256Digest
    source_binding_sha256: Sha256Digest
    status: Literal["available", "unavailable", "unverified"]
    can_report_unavailable: bool
    probe_sha256: Sha256Digest
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def require_status_contract(self) -> Self:
        expected = self.status == "unavailable"
        if self.can_report_unavailable is not expected:
            raise ValueError("Browser evidence status contract is invalid")
        return self


class BrowserEvidenceProof(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    recorded_at: AwareDatetime
    evidence_sha256: Sha256Digest
    source_binding_sha256: Sha256Digest


def browser_session_binding_sha256(session_id: str) -> str:
    return _sha256([session_id])


def browser_source_binding_sha256(
    session_id: str,
    turn_id: str,
    tool_use_id: str,
) -> str:
    return _sha256([session_id, turn_id, tool_use_id])


def read_latest_browser_evidence(
    evidence_dir: Path,
    *,
    expected_session_binding_sha256: str,
    not_before: datetime,
    now: datetime,
) -> BrowserEvidenceProof:
    if not_before.tzinfo is None or now.tzinfo is None:
        raise BrowserEvidenceSourceError("Browser evidence time boundary is naive")
    try:
        candidates = tuple(evidence_dir.glob("*.json"))
    except OSError as exc:
        raise BrowserEvidenceSourceError(
            "Browser evidence directory is unavailable"
        ) from exc
    if not candidates:
        raise BrowserEvidenceSourceError("Browser evidence is missing")
    if len(candidates) > _MAX_SOURCE_FILES:
        raise BrowserEvidenceSourceError("Browser evidence directory is oversized")
    try:
        path = max(candidates, key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name))
        if path.is_symlink() or path.resolve(strict=True).parent != evidence_dir.resolve(
            strict=True
        ):
            raise BrowserEvidenceSourceError(
                "Latest Browser evidence path is not a regular local file"
            )
        stat = path.stat()
        source = BrowserEvidenceSource.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except BrowserEvidenceSourceError:
        raise
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise BrowserEvidenceSourceError("Latest Browser evidence is malformed") from exc
    if path.stem != source.file_binding_sha256:
        raise BrowserEvidenceSourceError("Browser evidence filename binding is invalid")
    if source.probe_sha256 != EXPECTED_BROWSER_PROBE_SHA256:
        raise BrowserEvidenceSourceError("Browser evidence probe binding is invalid")
    if (
        source.session_binding_sha256 != expected_session_binding_sha256
    ):
        raise BrowserEvidenceSourceError("Browser evidence session binding is invalid")
    recorded_at = source.recorded_at.astimezone(UTC)
    boundary = not_before.astimezone(UTC)
    current = now.astimezone(UTC)
    modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    if recorded_at <= boundary or current - recorded_at > _MAX_SOURCE_AGE:
        raise BrowserEvidenceSourceError("Browser evidence is stale")
    if recorded_at > current + _MAX_FUTURE_SKEW:
        raise BrowserEvidenceSourceError("Browser evidence is from the future")
    if abs(modified_at - recorded_at) > _MAX_MTIME_SKEW:
        raise BrowserEvidenceSourceError("Browser evidence file time is inconsistent")
    if source.status != "available" or source.can_report_unavailable:
        raise BrowserEvidenceSourceError("In-app Browser was not proven available")
    evidence = cast(object, source.model_dump(mode="json"))
    return BrowserEvidenceProof(
        recorded_at=recorded_at,
        evidence_sha256=_sha256(evidence),
        source_binding_sha256=source.source_binding_sha256,
    )


def _sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "BROWSER_EVIDENCE_PROTOCOL",
    "BrowserEvidenceProof",
    "BrowserEvidenceSource",
    "BrowserEvidenceSourceError",
    "EXPECTED_BROWSER_PROBE_SHA256",
    "browser_session_binding_sha256",
    "browser_source_binding_sha256",
    "read_latest_browser_evidence",
]
