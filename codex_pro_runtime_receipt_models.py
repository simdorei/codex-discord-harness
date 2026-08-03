from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

RUNTIME_RECEIPT_SCHEMA_VERSION = 1
EXPECTED_BRIDGE_PROTOCOL_VERSION = 10
EXPECTED_MCP_TOOL_COUNT = 47
REQUIRED_TERMINAL_TOOL_CALLS = (
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_keys",
    "terminal_window_interrupt",
)
REQUIRED_TERMINAL_TOOL_CALL_COUNTS = {
    "terminal_window_capture": 3,
    "terminal_window_type": 1,
    "terminal_window_keys": 1,
    "terminal_window_interrupt": 1,
}
RuntimeReceiptId = Annotated[
    str,
    Field(pattern=r"^rtrec_[a-f0-9]{64}$"),
]
Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
RepositoryRevision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
TerminalToolName = Literal[
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_keys",
    "terminal_window_interrupt",
]
TerminalToolAction = Literal["capture", "type", "keys", "interrupt"]


class RuntimeReceiptBase(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    receipt_id: RuntimeReceiptId
    repository_revision: RepositoryRevision
    plugin_version: str = Field(min_length=1, max_length=100)
    protocol_version: Literal[10] = EXPECTED_BRIDGE_PROTOCOL_VERSION
    inventory_sha256: Sha256Digest
    evidence_sha256: Sha256Digest
    recorded_at: AwareDatetime


class RuntimeReceiptContext(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    repository_revision: RepositoryRevision
    plugin_version: str = Field(min_length=1, max_length=100)
    inventory_sha256: Sha256Digest
    recorded_at: AwareDatetime


class InAppBrowserReceipt(RuntimeReceiptBase):
    receipt_type: Literal["in_app_browser"] = "in_app_browser"
    browser_type: Literal["iab"] = "iab"
    available: Literal[True] = True

    @model_validator(mode="after")
    def require_receipt_integrity(self) -> Self:
        _require_receipt_id(self, "in_app_browser")
        return self


class ChatGptToolExposureReceipt(RuntimeReceiptBase):
    receipt_type: Literal["chatgpt_tool_exposure"] = "chatgpt_tool_exposure"
    tool_count: Literal[47] = EXPECTED_MCP_TOOL_COUNT
    terminal_execute_present: Literal[True] = True
    terminal_interact_present: Literal[True] = True

    @model_validator(mode="after")
    def require_receipt_integrity(self) -> Self:
        _require_receipt_id(self, "chatgpt_tool_exposure")
        return self


class TerminalToolCallReceipt(RuntimeReceiptBase):
    receipt_type: Literal["terminal_tool_call"] = "terminal_tool_call"
    tool_name: TerminalToolName
    action: TerminalToolAction
    succeeded: Literal[True] = True
    observation_bound: bool
    observation_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_matching_tool_action(self) -> Self:
        _require_receipt_id(
            self,
            self.tool_name,
            (
                self.action,
                str(self.observation_bound),
                self.observation_sha256,
            ),
        )
        expected = self.tool_name.removeprefix("terminal_window_")
        if self.action != expected:
            raise ValueError("terminal tool action does not match tool name")
        if self.action == "capture" and self.observation_bound:
            raise ValueError("capture creates rather than consumes an observation")
        if self.action != "capture" and not self.observation_bound:
            raise ValueError("terminal action must be bound to an observation")
        return self


class PostRestartRuntimeReceipt(RuntimeReceiptBase):
    receipt_type: Literal["post_restart_runtime"] = "post_restart_runtime"
    resident_generation: int = Field(ge=1)
    resident_started_at: AwareDatetime
    plugin_fingerprint_sha256: Sha256Digest
    browser_plugin_version: str = Field(min_length=1, max_length=100)
    healthy: Literal[True] = True
    tool_count: Literal[47] = EXPECTED_MCP_TOOL_COUNT
    terminal_interact_present: Literal[True] = True

    @model_validator(mode="after")
    def require_receipt_after_runtime_start(self) -> Self:
        _require_receipt_id(
            self,
            "post_restart_runtime",
            (
                str(self.resident_generation),
                self.resident_started_at.isoformat(),
                self.plugin_fingerprint_sha256,
                self.browser_plugin_version,
                str(self.tool_count),
                str(self.terminal_interact_present),
            ),
        )
        if self.recorded_at < self.resident_started_at:
            raise ValueError("runtime receipt predates the resident generation")
        return self


RuntimeEvidenceReceipt = Annotated[
    InAppBrowserReceipt
    | ChatGptToolExposureReceipt
    | TerminalToolCallReceipt
    | PostRestartRuntimeReceipt,
    Field(discriminator="receipt_type"),
]


class RuntimeReceiptSet(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = RUNTIME_RECEIPT_SCHEMA_VERSION
    receipts: tuple[RuntimeEvidenceReceipt, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def require_unique_receipt_ids(self) -> Self:
        receipt_ids = tuple(receipt.receipt_id for receipt in self.receipts)
        if len(set(receipt_ids)) != len(receipt_ids):
            raise ValueError("runtime receipt IDs must be unique")
        evidence_hashes = tuple(
            receipt.evidence_sha256 for receipt in self.receipts
        )
        if len(set(evidence_hashes)) != len(evidence_hashes):
            raise ValueError("runtime receipt evidence must be unique")
        return self


def receipt_recorded_at(receipt: RuntimeEvidenceReceipt) -> datetime:
    return receipt.recorded_at


def runtime_receipt_id(
    discriminator: str,
    evidence_sha256: str,
    recorded_at: datetime,
    *,
    repository_revision: str,
    plugin_version: str,
    protocol_version: int,
    inventory_sha256: str,
    bindings: tuple[str, ...] = (),
) -> str:
    canonical = json.dumps(
        {
            "type": discriminator,
            "evidence_sha256": evidence_sha256,
            "recorded_at": recorded_at.isoformat(),
            "repository_revision": repository_revision,
            "plugin_version": plugin_version,
            "protocol_version": protocol_version,
            "inventory_sha256": inventory_sha256,
            "bindings": bindings,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"rtrec_{hashlib.sha256(canonical.encode()).hexdigest()}"


def _require_receipt_id(
    receipt: RuntimeReceiptBase,
    discriminator: str,
    bindings: tuple[str, ...] = (),
) -> None:
    expected = runtime_receipt_id(
        discriminator,
        receipt.evidence_sha256,
        receipt.recorded_at,
        repository_revision=receipt.repository_revision,
        plugin_version=receipt.plugin_version,
        protocol_version=receipt.protocol_version,
        inventory_sha256=receipt.inventory_sha256,
        bindings=bindings,
    )
    if receipt.receipt_id != expected:
        raise ValueError("runtime receipt integrity check failed")


__all__ = [
    "ChatGptToolExposureReceipt",
    "EXPECTED_BRIDGE_PROTOCOL_VERSION",
    "EXPECTED_MCP_TOOL_COUNT",
    "InAppBrowserReceipt",
    "PostRestartRuntimeReceipt",
    "REQUIRED_TERMINAL_TOOL_CALLS",
    "REQUIRED_TERMINAL_TOOL_CALL_COUNTS",
    "RUNTIME_RECEIPT_SCHEMA_VERSION",
    "RuntimeEvidenceReceipt",
    "RuntimeReceiptBase",
    "RuntimeReceiptContext",
    "RuntimeReceiptId",
    "RuntimeReceiptSet",
    "Sha256Digest",
    "TerminalToolAction",
    "TerminalToolCallReceipt",
    "TerminalToolName",
    "receipt_recorded_at",
    "runtime_receipt_id",
]
