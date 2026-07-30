from __future__ import annotations

import queue
import threading
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, final

from codex_remote_mcp_bridge import RemoteMcpBridge
from codex_remote_mcp_bridge_config import ProjectTicket, RemoteMcpBridgeConfig
from simdorei_mcp_common.messages import (
    BridgeHello,
    GatewayHello,
    ProjectAck,
    ProjectUpsert,
)


class ManualAckSocket:
    def __init__(self) -> None:
        self.inbound: queue.Queue[str] = queue.Queue()
        self.sent: list[str] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc_type, exc, traceback

    def send(self, message: str) -> None:
        self.sent.append(message)
        if '"type":"hello"' in message:
            _ = BridgeHello.model_validate_json(message)
            self.inbound.put(GatewayHello().model_dump_json())

    def recv(self, timeout: float | None = None) -> str:
        try:
            return self.inbound.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        return None


@final
class ManualAckConnector:
    def __init__(self, socket: ManualAckSocket) -> None:
        self.socket: ManualAckSocket = socket

    def __call__(
        self,
        config: RemoteMcpBridgeConfig,
    ) -> AbstractContextManager[ManualAckSocket]:
        _ = config
        return self.socket


def test_delayed_ack_cannot_confirm_a_refreshed_binding(tmp_path: Path) -> None:
    socket = ManualAckSocket()
    bridge = RemoteMcpBridge(
        _config(),
        connector=ManualAckConnector(socket),
        log=lambda _: None,
    )
    tickets: list[ProjectTicket] = []

    def register() -> None:
        tickets.append(
            bridge.register_project(
                "thread-1",
                "codex-pro-project-1",
                tmp_path,
            )
        )

    first_worker = threading.Thread(target=register)
    first_worker.start()
    first = _wait_for_project(socket.sent)
    _ack(socket, first)
    first_worker.join(timeout=2)
    assert not first_worker.is_alive()

    second_worker = threading.Thread(target=register)
    second_worker.start()
    second = _wait_for_project(socket.sent, excluding=first.binding_id)
    _ack(socket, first)
    second_worker.join(timeout=0.05)
    assert second_worker.is_alive()

    _ack(socket, second)
    second_worker.join(timeout=2)
    bridge.close()

    assert not second_worker.is_alive()
    assert len(tickets) == 2


def _ack(socket: ManualAckSocket, project: ProjectUpsert) -> None:
    socket.inbound.put(
        ProjectAck(
            project_scope=project.project_scope,
            binding_id=project.binding_id,
        ).model_dump_json()
    )


def _wait_for_project(
    messages: list[str],
    *,
    excluding: str | None = None,
) -> ProjectUpsert:
    deadline = datetime.now(UTC).timestamp() + 2
    while datetime.now(UTC).timestamp() < deadline:
        for message in messages:
            if '"type":"project_upsert"' not in message:
                continue
            project = ProjectUpsert.model_validate_json(message)
            if project.binding_id != excluding:
                return project
    raise AssertionError("expected project registration was not sent")


def _config() -> RemoteMcpBridgeConfig:
    return RemoteMcpBridgeConfig(
        bridge_url="wss://example.test/bridge",
        device_id="device-1",
        device_token="secret-token",
        binding_ttl_seconds=600,
        binding_ack_timeout_seconds=2,
        reconnect_delay_seconds=0.01,
    )
