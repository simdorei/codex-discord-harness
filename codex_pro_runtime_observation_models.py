from __future__ import annotations

from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from codex_pro_runtime_receipt_models import (
    EXPECTED_BRIDGE_PROTOCOL_VERSION,
    RepositoryRevision,
    Sha256Digest,
    TerminalToolAction,
    TerminalToolName,
)

RuntimeObservationId = Annotated[
    str,
    Field(pattern=r"^rtobs_[a-f0-9]{64}$"),
]


@unique
class RuntimeObservationPhase(StrEnum):
    EMPTY = "empty"
    WAITING_BROWSER = "waiting_browser"
    WAITING_TOOL_EXPOSURE = "waiting_tool_exposure"
    WAITING_TERMINAL = "waiting_terminal"
    READY_TO_EMIT = "ready_to_emit"
    INVALID = "invalid"


class RuntimeObservationRelease(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    repository_revision: RepositoryRevision
    plugin_version: str = Field(min_length=1, max_length=100)
    protocol_version: int = Field(ge=1)
    inventory_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_current_protocol(self) -> RuntimeObservationRelease:
        if self.protocol_version != EXPECTED_BRIDGE_PROTOCOL_VERSION:
            raise ValueError("runtime observation protocol version is not current")
        return self


class RuntimeObservationBase(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    observation_id: RuntimeObservationId
    release: RuntimeObservationRelease
    evidence_sha256: Sha256Digest
    recorded_at: AwareDatetime


class PostRestartObservation(RuntimeObservationBase):
    kind: Literal["post_restart"] = "post_restart"
    resident_generation: int = Field(ge=1)
    resident_started_at: AwareDatetime
    plugin_fingerprint_sha256: Sha256Digest
    browser_plugin_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_started_before_observation(self) -> PostRestartObservation:
        if self.recorded_at < self.resident_started_at:
            raise ValueError("post-restart observation predates the resident runtime")
        return self


class BrowserObservation(RuntimeObservationBase):
    kind: Literal["browser"] = "browser"
    browser_type: Literal["iab"] = "iab"
    available: Literal[True] = True


class ToolExposureObservation(RuntimeObservationBase):
    kind: Literal["tool_exposure"] = "tool_exposure"
    session_binding_sha256: Sha256Digest
    tool_count: Literal[47] = 47
    terminal_execute_present: Literal[True] = True
    terminal_interact_present: Literal[True] = True


class TerminalObservation(RuntimeObservationBase):
    kind: Literal["terminal"] = "terminal"
    session_binding_sha256: Sha256Digest
    tool_name: TerminalToolName
    action: TerminalToolAction
    observation_bound: bool
    observation_sha256: Sha256Digest

    @model_validator(mode="after")
    def require_tool_action_contract(self) -> TerminalObservation:
        expected = self.tool_name.removeprefix("terminal_window_")
        if self.action != expected:
            raise ValueError("terminal observation action does not match its tool")
        if self.action == "capture" and self.observation_bound:
            raise ValueError("terminal capture creates an observation")
        if self.action != "capture" and not self.observation_bound:
            raise ValueError("terminal action must consume an observation")
        return self


RuntimeObservation = (
    PostRestartObservation
    | BrowserObservation
    | ToolExposureObservation
    | TerminalObservation
)


class RuntimeObservationSnapshot(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    phase: RuntimeObservationPhase
    observed_count: int = Field(ge=0, le=9)
    terminal_progress: int = Field(ge=0, le=6)
    ready: bool
    failure_code: str | None = Field(default=None, max_length=100)
    last_recorded_at: datetime | None = None
    receipt_emitted: bool = False
    receipt_error: str | None = Field(default=None, max_length=100)


__all__ = [
    "BrowserObservation",
    "PostRestartObservation",
    "RuntimeObservation",
    "RuntimeObservationBase",
    "RuntimeObservationId",
    "RuntimeObservationPhase",
    "RuntimeObservationRelease",
    "RuntimeObservationSnapshot",
    "TerminalObservation",
    "ToolExposureObservation",
]
