from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
ObservedTerminalTool = Literal[
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_keys",
    "terminal_window_interrupt",
]


class RuntimeProvenanceEnvelope(BaseModel):
    """Public-safe identity attached by the authenticated gateway."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    session_binding_sha256: Sha256Digest
    cycle_binding_sha256: Sha256Digest | None = None


def runtime_session_binding_sha256(session: str, subject: str) -> str:
    return _sha256(["chatgpt-session-v1", session, subject])


def runtime_route_binding_sha256(
    thread_id: str,
    computer_session_id: str,
    session_binding_sha256: str,
) -> str:
    return _sha256(
        [
            "runtime-route-v1",
            thread_id,
            computer_session_id,
            session_binding_sha256,
        ]
    )


def terminal_observation_sha256(observation_id: str) -> str:
    return _sha256({"observation_id": observation_id})


def terminal_runtime_evidence_sha256(
    *,
    tool_name: ObservedTerminalTool,
    observation_sha256: str,
    identity_digest: str,
    recorded_at: datetime,
) -> str:
    return _sha256(
        {
            "protocol": "terminal-runtime-evidence-v1",
            "tool_name": tool_name,
            "observation_sha256": observation_sha256,
            "identity_digest": identity_digest,
            "recorded_at": recorded_at.isoformat(),
        }
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
    "ObservedTerminalTool",
    "RuntimeProvenanceEnvelope",
    "Sha256Digest",
    "runtime_route_binding_sha256",
    "runtime_session_binding_sha256",
    "terminal_observation_sha256",
    "terminal_runtime_evidence_sha256",
]
