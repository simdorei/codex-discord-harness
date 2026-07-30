from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, final

import anyio

from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from simdorei_mcp_common.messages import (
    BridgeResult,
    DeviceId,
    GatewayCommand,
    ProjectUpsert,
)


class BridgeSender(Protocol):
    async def send(self, command: GatewayCommand) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingProject:
    device_id: DeviceId
    value: ProjectUpsert


@dataclass(frozen=True, slots=True)
class SessionRoute:
    session: str
    device_id: DeviceId
    thread_id: str
    subject: str
    computer_session_id: str
    expires_at: datetime


@final
class PendingCall:
    """Mutable rendezvous for one in-flight local bridge request."""

    __slots__ = (
        "computer_session_id",
        "device_id",
        "event",
        "failure",
        "result",
    )

    def __init__(
        self,
        *,
        event: anyio.Event,
        computer_session_id: str,
        result: BridgeResult | None = None,
        device_id: DeviceId | None = None,
        failure: BrokerError | None = None,
    ) -> None:
        self.event = event
        self.computer_session_id = computer_session_id
        self.result = result
        self.device_id = device_id
        self.failure = failure
