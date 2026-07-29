from __future__ import annotations

import queue
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, assert_never

from pydantic import ValidationError
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from codex_remote_mcp_bridge_config import BindingTicket, RemoteMcpBridgeConfig
from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    BindingAck,
    BindingUpsert,
    BridgeHello,
    DeviceId,
    GatewayCommand,
    GatewayHello,
    ListFilesCommand,
    ProjectInfoCommand,
    ReadFileCommand,
    WriteFileCommand,
    parse_gateway_message,
)

LogFunc = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class RemoteMcpBridgeError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


class BridgeSocket(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...


BridgeConnector = Callable[
    [RemoteMcpBridgeConfig],
    AbstractContextManager[BridgeSocket],
]


class RemoteMcpBridge:  # MUTABLE_OK: owns synchronized connection state.
    """Maintains one outbound bridge and dispatches bounded file operations."""

    def __init__(
        self,
        config: RemoteMcpBridgeConfig,
        *,
        connector: BridgeConnector | None = None,
        log: LogFunc,
    ) -> None:
        self._config = config
        self._connector = connector or _connect
        self._log = log
        self._dispatcher = LocalProjectDispatcher()
        self._condition = threading.Condition()
        self._outbound: queue.Queue[BindingUpsert] = queue.Queue()
        self._active: dict[str, BindingUpsert] = {}
        self._acked: set[str] = set()
        self._last_error = ""
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def issue_binding(self, thread_id: str, root: Path) -> BindingTicket:
        if not thread_id:
            raise RemoteMcpBridgeError("A Codex thread is required for local project binding.")
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self._config.binding_ttl_seconds
        )
        code = secrets.token_urlsafe(32)
        binding = BindingUpsert(
            binding_code=code,
            thread_id=thread_id,
            project_name=root.resolve().name or "local-project",
            expires_at=expires_at,
        )
        self._dispatcher.upsert(thread_id, root, expires_at)
        with self._condition:
            self._active[code] = binding
            if self._connected:
                self._outbound.put(binding)
            self._ensure_started()
            acknowledged = self._condition.wait_for(
                lambda: code in self._acked,
                timeout=self._config.binding_ack_timeout_seconds,
            )
            if not acknowledged:
                detail = f" Last connection error: {self._last_error}" if self._last_error else ""
                raise RemoteMcpBridgeError(
                    "The local project bridge did not acknowledge the binding in time."
                    + detail
                )
        return BindingTicket(binding_code=code, expires_at=expires_at)

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)

    def _ensure_started(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="codex-remote-mcp-bridge",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._connector(self._config) as socket:
                    self._serve_connection(socket)
            except (OSError, ValidationError, WebSocketException, RemoteMcpBridgeError) as exc:
                with self._condition:
                    self._last_error = str(exc)
                    self._connected = False
                    self._condition.notify_all()
                self._log(f"remote_mcp_bridge_disconnected error={type(exc).__name__}")
            self._stop.wait(self._config.reconnect_delay_seconds)

    def _serve_connection(self, socket: BridgeSocket) -> None:
        socket.send(
            BridgeHello(device_id=DeviceId(self._config.device_id)).model_dump_json()
        )
        first = _receive(socket)
        if not isinstance(first, GatewayHello):
            raise RemoteMcpBridgeError("The gateway did not acknowledge the bridge hello.")
        with self._condition:
            self._connected = True
            self._last_error = ""
            bindings = tuple(self._active.values())
            self._condition.notify_all()
        for binding in bindings:
            socket.send(binding.model_dump_json())
        self._log("remote_mcp_bridge_connected")
        while not self._stop.is_set():
            self._send_queued_bindings(socket)
            try:
                message = _receive(socket, timeout=0.25)
            except TimeoutError:
                continue
            match message:
                case BindingAck(binding_code=code):
                    with self._condition:
                        self._acked.add(code)
                        self._condition.notify_all()
                case (
                    ProjectInfoCommand()
                    | ListFilesCommand()
                    | ReadFileCommand()
                    | WriteFileCommand()
                ):
                    socket.send(self._dispatcher.execute(message).model_dump_json())
                case GatewayHello():
                    raise RemoteMcpBridgeError("The gateway sent a duplicate hello.")
                case unreachable:
                    assert_never(unreachable)

    def _send_queued_bindings(self, socket: BridgeSocket) -> None:
        while True:
            try:
                binding = self._outbound.get_nowait()
            except queue.Empty:
                return
            socket.send(binding.model_dump_json())


@contextmanager
def _connect(config: RemoteMcpBridgeConfig) -> Iterator[BridgeSocket]:
    with connect(
        config.bridge_url,
        additional_headers={"Authorization": f"Bearer {config.device_token}"},
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_200_000,
    ) as socket:
        yield socket


def _receive(
    socket: BridgeSocket,
    *,
    timeout: float | None = None,
) -> GatewayHello | BindingAck | GatewayCommand:
    raw = socket.recv(timeout=timeout)
    if not isinstance(raw, str):
        raise RemoteMcpBridgeError("The gateway sent an unsupported binary message.")
    return parse_gateway_message(raw)
