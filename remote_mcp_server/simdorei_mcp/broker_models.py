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
from simdorei_mcp_common.leases import RenewableExpiry


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
    computer_session_generation: int
    lease: RenewableExpiry

    @property
    def expires_at(self) -> datetime:
        return self.lease.value

    def renew(self, expires_at: datetime) -> None:
        self.lease.extend(expires_at)


@final
class PendingCall:
    """Mutable rendezvous for one in-flight local bridge request."""

    __slots__ = (
        "computer_session_id",
        "device_id",
        "event",
        "failure",
        "fingerprint",
        "result",
        "waiter_count",
    )

    def __init__(
        self,
        *,
        event: anyio.Event,
        computer_session_id: str,
        fingerprint: str,
        result: BridgeResult | None = None,
        device_id: DeviceId | None = None,
        failure: BrokerError | None = None,
    ) -> None:
        self.event = event
        self.computer_session_id = computer_session_id
        self.fingerprint = fingerprint
        self.result = result
        self.device_id = device_id
        self.failure = failure
        self.waiter_count = 1
