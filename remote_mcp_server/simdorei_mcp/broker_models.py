from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import anyio

from simdorei_mcp_common.messages import (
    BridgeResult,
    DeviceId,
    GatewayCommand,
    ProjectUpsert,
)


class BridgeSender(Protocol):
    async def send(self, command: GatewayCommand) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingProject:
    device_id: DeviceId
    value: ProjectUpsert


@dataclass(frozen=True, slots=True)
class SessionRoute:
    device_id: DeviceId
    thread_id: str
    subject: str
    expires_at: datetime


@dataclass(slots=True)  # MUTABLE_OK: one in-flight request rendezvous.
class PendingCall:
    """Mutable rendezvous for one in-flight local bridge request."""

    event: anyio.Event
    result: BridgeResult | None = None
    device_id: DeviceId | None = None
