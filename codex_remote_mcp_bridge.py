from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import assert_never, final
from uuid import uuid4

from pydantic import ValidationError
from websockets.exceptions import WebSocketException

from codex_remote_mcp_bridge_config import ProjectTicket, RemoteMcpBridgeConfig
from codex_remote_mcp_bridge_connection import (
    BridgeConnector,
    BridgeSocket,
    RemoteMcpBridgeError,
    SerializedBridgeConnection,
    connect_bridge,
    invalidate_sessions,
    receive_message,
)
from codex_remote_mcp_bridge_projects import (
    prune_expired_projects,
    replace_thread_project,
)
from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    BridgeHello,
    DeviceId,
    GatewayHello,
    ListFilesCommand,
    ProjectAck,
    ProjectInfoCommand,
    ProjectOperationCommand,
    ProjectSessionCommand,
    ProjectUpsert,
    ReadFileCommand,
    WriteFileCommand,
)

LogFunc = Callable[[str], None]


NowFactory = Callable[[], datetime]


@final
class RemoteMcpBridge:  # MUTABLE_OK: owns synchronized connection state.
    """Maintains one outbound bridge and dispatches bounded file operations."""

    def __init__(
        self,
        config: RemoteMcpBridgeConfig,
        *,
        connector: BridgeConnector | None = None,
        dispatcher: LocalProjectDispatcher | None = None,
        log: LogFunc,
        now: NowFactory = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._connector = connector or connect_bridge
        self._log = log
        self._now = now
        self._dispatcher = dispatcher or LocalProjectDispatcher()
        self._condition = threading.Condition()
        self._outbound: queue.Queue[ProjectUpsert] = queue.Queue()
        self._active: dict[str, ProjectUpsert] = {}
        self._acked: dict[str, str] = {}
        self._last_error = ""
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket_lock = threading.Lock()
        self._active_socket: BridgeSocket | None = None

    def register_project(
        self,
        thread_id: str,
        project_scope: str,
        root: Path,
    ) -> ProjectTicket:
        if not thread_id:
            raise RemoteMcpBridgeError(
                "A Codex thread is required for project registration."
            )
        if not project_scope:
            raise RemoteMcpBridgeError(
                "A project scope is required for project registration."
            )
        expires_at = self._now() + timedelta(seconds=self._config.binding_ttl_seconds)
        project = ProjectUpsert(
            project_scope=project_scope,
            binding_id=uuid4().hex,
            thread_id=thread_id,
            project_name=root.resolve().name or "local-project",
            expires_at=expires_at,
        )
        self._dispatcher.upsert(thread_id, root, expires_at)
        with self._condition:
            self._prune_expired_locked()
            replace_thread_project(self._active, self._acked, project)
            if self._connected:
                self._outbound.put(project)
            self._ensure_started()
            acknowledged = self._condition.wait_for(
                lambda: (
                    self._acked.get(project_scope) == project.binding_id
                    or self._stop.is_set()
                ),
                timeout=self._config.binding_ack_timeout_seconds,
            )
            if not acknowledged or self._acked.get(project_scope) != project.binding_id:
                detail = (
                    f" Last connection error: {self._last_error}"
                    if self._last_error
                    else ""
                )
                raise RemoteMcpBridgeError(
                    "The local project bridge did not acknowledge the binding in time."
                    + detail
                )
        return ProjectTicket(project_scope=project_scope, expires_at=expires_at)

    def close(self) -> None:
        self._stop.set()
        with self._condition:
            self._connected = False
            self._acked.clear()
            self._condition.notify_all()
        with self._socket_lock:
            socket = self._active_socket
        if socket is not None:
            try:
                socket.close()
            except (OSError, WebSocketException) as exc:
                self._log(f"remote_mcp_bridge_close_failed error={type(exc).__name__}")
        _ = invalidate_sessions(self._dispatcher, self._log)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=12)
        cleanup_complete = invalidate_sessions(self._dispatcher, self._log)
        if thread is not None and thread.is_alive():
            raise RemoteMcpBridgeError("The local project bridge did not stop in time.")
        if not cleanup_complete:
            raise RemoteMcpBridgeError(
                "The local project bridge could not close every session-owned app."
            )

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
                with SerializedBridgeConnection(
                    self._connector(self._config)
                ) as socket:
                    with self._socket_lock:
                        self._active_socket = socket
                    try:
                        self._serve_connection(socket)
                    finally:
                        with self._socket_lock:
                            if self._active_socket is socket:
                                self._active_socket = None
                        _ = invalidate_sessions(self._dispatcher, self._log)
            except (
                OSError,
                ValidationError,
                WebSocketException,
                RemoteMcpBridgeError,
            ) as exc:
                with self._condition:
                    self._last_error = str(exc)
                    self._connected = False
                    self._acked.clear()
                    self._condition.notify_all()
                self._log(f"remote_mcp_bridge_disconnected error={type(exc).__name__}")
            _ = self._stop.wait(self._config.reconnect_delay_seconds)

    def _serve_connection(self, socket: BridgeSocket) -> None:
        socket.send(
            BridgeHello(
                protocol_version=5,
                device_id=DeviceId(self._config.device_id),
            ).model_dump_json()
        )
        while not self._stop.is_set():
            try:
                first = receive_message(socket, timeout=0.25)
            except TimeoutError:
                continue
            break
        else:
            return
        if not isinstance(first, GatewayHello):
            raise RemoteMcpBridgeError(
                "The gateway did not acknowledge the bridge hello."
            )
        with self._condition:
            self._connected = True
            self._last_error = ""
            self._acked.clear()
            self._prune_expired_locked()
            projects = tuple(self._active.values())
            self._condition.notify_all()
        for project in projects:
            socket.send(project.model_dump_json())
        self._log("remote_mcp_bridge_connected")
        while not self._stop.is_set():
            self._send_queued_projects(socket)
            try:
                message = receive_message(socket, timeout=0.25)
            except TimeoutError:
                continue
            match message:
                case ProjectAck(project_scope=project_scope, binding_id=binding_id):
                    with self._condition:
                        project = self._active.get(project_scope)
                        if (
                            project is not None
                            and project.binding_id == binding_id
                            and project.expires_at > self._now()
                        ):
                            self._acked[project_scope] = binding_id
                        self._condition.notify_all()
                case (
                    ProjectInfoCommand()
                    | ListFilesCommand()
                    | ReadFileCommand()
                    | WriteFileCommand()
                    | ProjectOperationCommand()
                    | ProjectSessionCommand()
                ):
                    socket.send(self._dispatcher.execute(message).model_dump_json())
                case GatewayHello():
                    raise RemoteMcpBridgeError("The gateway sent a duplicate hello.")
                case _:
                    assert_never(message)

    def _send_queued_projects(self, socket: BridgeSocket) -> None:
        while True:
            try:
                project = self._outbound.get_nowait()
            except queue.Empty:
                return
            with self._condition:
                self._prune_expired_locked()
                current = self._active.get(project.project_scope)
            if current == project:
                socket.send(project.model_dump_json())

    def _prune_expired_locked(self) -> None:
        prune_expired_projects(self._active, self._acked, self._now())
