from __future__ import annotations

import queue
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from codex_remote_mcp_bridge import RemoteMcpBridge
from codex_remote_mcp_bridge_config import RemoteMcpBridgeConfig
from simdorei_mcp_common.messages import (
    BindingAck,
    BindingUpsert,
    BridgeHello,
    GatewayHello,
    ProjectInfoCommand,
    ProjectInfoResult,
    RequestId,
)


class FakeSocket:
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
            BridgeHello.model_validate_json(message)
            self.inbound.put(GatewayHello().model_dump_json())
        if '"type":"binding_upsert"' in message:
            binding = BindingUpsert.model_validate_json(message)
            self.inbound.put(
                BindingAck(binding_code=binding.binding_code).model_dump_json()
            )

    def recv(self, timeout: float | None = None) -> str:
        try:
            return self.inbound.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc


class FakeConnector:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket

    def __call__(
        self,
        config: RemoteMcpBridgeConfig,
    ) -> AbstractContextManager[FakeSocket]:
        _ = config
        return self.socket


def _config() -> RemoteMcpBridgeConfig:
    return RemoteMcpBridgeConfig(
        bridge_url="wss://example.test/bridge",
        device_id="device-1",
        device_token="secret-token",
        binding_ttl_seconds=600,
        binding_ack_timeout_seconds=2,
        reconnect_delay_seconds=0.01,
    )


def test_bridge_registers_binding_and_dispatches_project_info(tmp_path: Path) -> None:
    # Given
    socket = FakeSocket()
    bridge = RemoteMcpBridge(
        _config(),
        connector=FakeConnector(socket),
        log=lambda _: None,
    )

    # When
    ticket = bridge.issue_binding("thread-1", tmp_path)
    socket.inbound.put(
        ProjectInfoCommand(
            request_id=RequestId("request-1"),
            thread_id="thread-1",
        ).model_dump_json()
    )
    result_json = _wait_for_sent(
        socket.sent,
        lambda raw: '"type":"project_info_result"' in raw,
    )
    bridge.close()

    # Then
    assert len(ticket.binding_code) >= 24
    binding = next(
        BindingUpsert.model_validate_json(raw)
        for raw in socket.sent
        if '"type":"binding_upsert"' in raw
    )
    assert binding.thread_id == "thread-1"
    result = ProjectInfoResult.model_validate_json(result_json)
    assert Path(result.output.root) == tmp_path.resolve()


def _wait_for_sent(messages: list[str], predicate: Callable[[str], bool]) -> str:
    deadline = datetime.now(UTC).timestamp() + 2
    while datetime.now(UTC).timestamp() < deadline:
        for message in messages:
            if predicate(message):
                return message
    raise AssertionError("expected bridge message was not sent")
