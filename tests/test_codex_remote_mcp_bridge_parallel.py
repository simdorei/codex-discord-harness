from __future__ import annotations

import queue
import threading
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self, final

import pytest

from codex_remote_mcp_bridge import RemoteMcpBridge
from codex_remote_mcp_bridge_config import RemoteMcpBridgeConfig
from codex_remote_mcp_computer import ComputerController
from codex_remote_mcp_dispatch_commands import (
    BoundProjectCommand,
    execute_bound_project_command,
)
from codex_remote_mcp_files import ProjectFileAccess
from simdorei_mcp_common.messages import (
    BridgeHello,
    BridgeResult,
    GatewayHello,
    ProjectAck,
    ProjectSessionCommand,
    ProjectSessionResult,
    ProjectUpsert,
    ReadFileCommand,
    ReadFileResult,
    RequestId,
)


class ParallelSocket:
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
        if '"type":"project_upsert"' in message:
            project = ProjectUpsert.model_validate_json(message)
            self.inbound.put(
                ProjectAck(
                    project_scope=project.project_scope,
                    binding_id=project.binding_id,
                ).model_dump_json()
            )

    def recv(self, timeout: float | None = None) -> str:
        try:
            return self.inbound.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        return None


@final
class ParallelConnector:
    def __init__(self, socket: ParallelSocket) -> None:
        self._socket = socket

    def __call__(
        self,
        config: RemoteMcpBridgeConfig,
    ) -> AbstractContextManager[ParallelSocket]:
        _ = config
        return self._socket


def test_two_project_reads_can_run_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "first.txt").write_text("first", encoding="utf-8")
    (tmp_path / "second.txt").write_text("second", encoding="utf-8")
    socket = ParallelSocket()
    bridge = RemoteMcpBridge(
        _config(),
        connector=ParallelConnector(socket),
        log=lambda _: None,
    )
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    original_execute = execute_bound_project_command

    def delayed_execute(
        command: BoundProjectCommand,
        access: ProjectFileAccess,
        computer: ComputerController | None,
    ) -> BridgeResult:
        if isinstance(command, ReadFileCommand):
            if command.request_id == "read-first":
                first_started.set()
                assert release_first.wait(timeout=5)
            if command.request_id == "read-second":
                second_started.set()
        return original_execute(command, access, computer)

    monkeypatch.setattr(
        "codex_remote_mcp_dispatch.execute_bound_project_command",
        delayed_execute,
    )

    try:
        _ = bridge.register_project(
            "thread-1",
            "codex-pro-project-parallel",
            tmp_path,
        )
        socket.inbound.put(
            ProjectSessionCommand(
                request_id=RequestId("activate-parallel-session"),
                thread_id="thread-1",
                computer_session_id="parallel-session-id",
            ).model_dump_json()
        )
        _wait_for_result(socket.sent, ProjectSessionResult)
        for request_id, path in (
            ("read-first", "first.txt"),
            ("read-second", "second.txt"),
        ):
            socket.inbound.put(
                ReadFileCommand(
                    request_id=RequestId(request_id),
                    thread_id="thread-1",
                    computer_session_id="parallel-session-id",
                    path=path,
                    start_line=1,
                    max_lines=10,
                ).model_dump_json()
            )

        assert first_started.wait(timeout=2)
        assert second_started.wait(timeout=0.5)
        release_first.set()
        _wait_for_result_count(socket.sent, ReadFileResult, expected=2)
    finally:
        release_first.set()
        bridge.close()


def _wait_for_result(
    messages: list[str],
    result_type: type[ProjectSessionResult],
) -> str:
    return _wait_for_result_count(messages, result_type, expected=1)


def _wait_for_result_count(
    messages: list[str],
    result_type: type[ProjectSessionResult] | type[ReadFileResult],
    *,
    expected: int,
) -> str:
    deadline = datetime.now(UTC).timestamp() + 2
    while datetime.now(UTC).timestamp() < deadline:
        matching = [
            message
            for message in messages
            if f'"type":"{result_type.model_fields["type"].default}"' in message
        ]
        if len(matching) >= expected:
            return matching[-1]
    raise AssertionError(f"expected {expected} {result_type.__name__} messages")


def _config() -> RemoteMcpBridgeConfig:
    return RemoteMcpBridgeConfig(
        bridge_url="wss://example.test/bridge",
        device_id="parallel-device",
        device_token="secret-token",
        binding_ttl_seconds=600,
        binding_ack_timeout_seconds=2,
        reconnect_delay_seconds=0.01,
    )
