from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, final
from uuid import uuid4

import anyio

from remote_mcp_server.simdorei_mcp.broker_errors import (
    ActiveBindingMissingError,
    BindingCodeError,
    BridgeTimeoutError,
    BridgeUnavailableError,
    SessionProjectConflictError,
)
from remote_mcp_server.simdorei_mcp.broker_results import (
    list_files_output,
    project_info_output,
    read_file_output,
    write_file_output,
)
from simdorei_mcp_common.messages import (
    BindingUpsert,
    BindProjectOutput,
    BridgeResult,
    DeviceId,
    GatewayCommand,
    ListFilesCommand,
    ListFilesOutput,
    ProjectInfoCommand,
    ProjectInfoOutput,
    ReadFileCommand,
    ReadFileOutput,
    RequestId,
    WriteFileCommand,
    WriteFileOutput,
)


class BridgeSender(Protocol):
    async def send(self, command: GatewayCommand) -> None: ...


@dataclass(frozen=True, slots=True)
class PendingBinding:
    device_id: DeviceId
    value: BindingUpsert


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


@final
class BindingBroker:  # MUTABLE_OK: owns synchronized live routing state.
    """Owns live device connections, one-time codes, and session routes."""

    def __init__(self, *, request_timeout_seconds: float = 30.0) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._lock = anyio.Lock()
        self._devices: dict[DeviceId, BridgeSender] = {}
        self._codes: dict[str, PendingBinding] = {}
        self._sessions: dict[str, SessionRoute] = {}
        self._thread_sessions: dict[tuple[DeviceId, str], str] = {}
        self._pending: dict[RequestId, PendingCall] = {}

    async def attach(self, device_id: DeviceId, sender: BridgeSender) -> None:
        async with self._lock:
            self._devices[device_id] = sender

    async def detach(self, device_id: DeviceId, sender: BridgeSender) -> None:
        async with self._lock:
            current = self._devices.get(device_id)
            if current is not sender:
                return
            del self._devices[device_id]
            stale_codes = [
                code
                for code, binding in self._codes.items()
                if binding.device_id == device_id
            ]
            stale_sessions = [
                session
                for session, route in self._sessions.items()
                if route.device_id == device_id
            ]
            for code in stale_codes:
                del self._codes[code]
            for session in stale_sessions:
                route = self._sessions.pop(session)
                _ = self._thread_sessions.pop(
                    (route.device_id, route.thread_id),
                    None,
                )

    async def upsert(self, device_id: DeviceId, binding: BindingUpsert) -> None:
        if binding.expires_at <= datetime.now(UTC):
            raise BindingCodeError("binding code is already expired")
        async with self._lock:
            if device_id not in self._devices:
                raise BridgeUnavailableError("local bridge is disconnected")
            self._codes[binding.binding_code] = PendingBinding(
                device_id=device_id,
                value=binding,
            )

    async def bind(self, session: str, subject: str, code: str) -> BindProjectOutput:
        async with self._lock:
            pending = self._codes.get(code)
            if pending is None or pending.value.expires_at <= datetime.now(UTC):
                raise BindingCodeError("binding code is invalid or expired")
            route = SessionRoute(
                device_id=pending.device_id,
                thread_id=pending.value.thread_id,
                subject=subject,
                expires_at=pending.value.expires_at,
            )
            current = self._sessions.get(session)
            if (
                current is not None
                and current.expires_at > datetime.now(UTC)
                and (
                    current.device_id != route.device_id
                    or current.thread_id != route.thread_id
                    or current.subject != route.subject
                )
            ):
                conflict_message = (
                    "This ChatGPT conversation is already connected to a different "
                    + "Codex thread. Open a new ChatGPT conversation and bind there."
                )
                raise SessionProjectConflictError(
                    conflict_message
                )
            _ = self._codes.pop(code)
            previous = self._thread_sessions.get((route.device_id, route.thread_id))
            if previous is not None and previous != session:
                _ = self._sessions.pop(previous, None)
            self._sessions[session] = route
            self._thread_sessions[(route.device_id, route.thread_id)] = session
        return BindProjectOutput(
            project_name=pending.value.project_name,
            thread_id=route.thread_id,
            expires_at=route.expires_at,
        )

    async def project_info(self, session: str, subject: str) -> ProjectInfoOutput:
        route, sender = await self._route(session, subject)
        result = await self._dispatch(
            route,
            sender,
            ProjectInfoCommand(
                request_id=RequestId(uuid4().hex),
                thread_id=route.thread_id,
            )
        )
        return project_info_output(result)

    async def list_files(
        self,
        session: str,
        subject: str,
        *,
        pattern: str,
        limit: int,
    ) -> ListFilesOutput:
        route, sender = await self._route(session, subject)
        result = await self._dispatch(
            route,
            sender,
            ListFilesCommand(
                request_id=RequestId(uuid4().hex),
                thread_id=route.thread_id,
                pattern=pattern,
                limit=limit,
            ),
        )
        return list_files_output(result)

    async def read_file(
        self,
        session: str,
        subject: str,
        command: ReadFileCommand,
    ) -> ReadFileOutput:
        route, sender = await self._route(session, subject)
        routed = command.model_copy(update={"thread_id": route.thread_id})
        result = await self._dispatch(route, sender, routed)
        return read_file_output(result)

    async def write_file(
        self,
        session: str,
        subject: str,
        command: WriteFileCommand,
    ) -> WriteFileOutput:
        route, sender = await self._route(session, subject)
        routed = command.model_copy(update={"thread_id": route.thread_id})
        result = await self._dispatch(route, sender, routed)
        return write_file_output(result)

    async def complete(self, device_id: DeviceId, result: BridgeResult) -> None:
        async with self._lock:
            pending = self._pending.get(result.request_id)
            if pending is None or pending.device_id != device_id:
                return
            pending.result = result
            pending.event.set()

    async def _dispatch(
        self,
        route: SessionRoute,
        sender: BridgeSender,
        command: GatewayCommand,
    ) -> BridgeResult:
        pending = PendingCall(event=anyio.Event(), device_id=route.device_id)
        async with self._lock:
            self._pending[command.request_id] = pending
        try:
            await sender.send(command)
            with anyio.fail_after(self._request_timeout_seconds):
                await pending.event.wait()
        except TimeoutError as exc:
            raise BridgeTimeoutError("local bridge response timed out") from exc
        finally:
            async with self._lock:
                _ = self._pending.pop(command.request_id, None)
        if pending.result is None:
            raise BridgeUnavailableError("local bridge returned no result")
        return pending.result

    async def _route(
        self,
        session: str,
        subject: str,
    ) -> tuple[SessionRoute, BridgeSender]:
        async with self._lock:
            route = self._sessions.get(session)
            if (
                route is None
                or route.expires_at <= datetime.now(UTC)
                or route.subject != subject
            ):
                raise ActiveBindingMissingError("ChatGPT session is not bound to an active project")
            sender = self._devices.get(route.device_id)
            if sender is None:
                raise BridgeUnavailableError("bound local bridge is disconnected")
            return route, sender
