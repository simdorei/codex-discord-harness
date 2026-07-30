from __future__ import annotations

from datetime import UTC, datetime
from typing import final, override
from uuid import uuid4

import anyio

from remote_mcp_server.simdorei_mcp.broker_errors import (
    ActiveBindingMissingError,
    BindingCodeError,
    BridgeTimeoutError,
    BridgeUnavailableError,
)
from remote_mcp_server.simdorei_mcp.broker_models import (
    BridgeSender,
    PendingCall,
    PendingProject,
    SessionRoute,
)
from remote_mcp_server.simdorei_mcp.broker_registration import (
    disconnected_device_targets,
    stale_registration_targets,
)
from remote_mcp_server.simdorei_mcp.broker_requests import BrokerRequestsMixin
from remote_mcp_server.simdorei_mcp.broker_results import require_project_session_result
from remote_mcp_server.simdorei_mcp.broker_routes import BrokerRouteRegistry
from simdorei_mcp_common.messages import (
    BridgeResult,
    DeviceId,
    GatewayCommand,
    ProjectSelectionOutput,
    ProjectSessionCommand,
    ProjectUpsert,
    RequestId,
)


@final
class BindingBroker(BrokerRequestsMixin):  # MUTABLE_OK: synchronized routing state.
    """Owns live devices, registered project scopes, and session routes."""

    def __init__(self, *, request_timeout_seconds: float = 30.0) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._lock = anyio.Lock()
        self._selection_lock = anyio.Lock()
        self._devices: dict[DeviceId, BridgeSender] = {}
        self._projects: dict[str, PendingProject] = {}
        self._sessions: dict[str, SessionRoute] = {}
        self._thread_sessions: dict[tuple[DeviceId, str], str] = {}
        self._pending: dict[RequestId, PendingCall] = {}
        self._routes = BrokerRouteRegistry(
            self._sessions,
            self._thread_sessions,
            self._pending,
        )

    @property
    def pending_call_count(self) -> int:
        return len(self._pending)

    async def attach(
        self,
        device_id: DeviceId,
        sender: BridgeSender,
    ) -> BridgeSender | None:
        async with self._lock:
            displaced = self._devices.get(device_id)
            if displaced is not sender:
                self._disconnect_device(device_id)
            self._devices[device_id] = sender
            return displaced if displaced is not sender else None

    async def detach(self, device_id: DeviceId, sender: BridgeSender) -> None:
        async with self._lock:
            current = self._devices.get(device_id)
            if current is not sender:
                return
            self._disconnect_device(device_id)

    async def upsert(
        self,
        device_id: DeviceId,
        sender: BridgeSender,
        project: ProjectUpsert,
    ) -> None:
        if project.expires_at <= datetime.now(UTC):
            raise BindingCodeError("project scope is already expired")
        async with self._lock:
            if self._devices.get(device_id) is not sender:
                raise BridgeUnavailableError("local bridge is disconnected")
            stale_scopes, stale_routes = stale_registration_targets(
                self._projects,
                self._sessions.values(),
                device_id,
                project,
            )
            for scope in stale_scopes:
                del self._projects[scope]
            for route in stale_routes:
                self._routes.remove(route)
            self._projects[project.project_scope] = PendingProject(
                device_id=device_id,
                value=project,
            )

    async def select(
        self,
        session: str,
        subject: str,
        project_scope: str,
    ) -> ProjectSelectionOutput:
        async with self._selection_lock:
            async with self._lock:
                pending = self._projects.get(project_scope)
                if pending is None or pending.value.expires_at <= datetime.now(UTC):
                    raise BindingCodeError("project scope is unavailable or expired")
                route = SessionRoute(
                    session=session,
                    device_id=pending.device_id,
                    thread_id=pending.value.thread_id,
                    subject=subject,
                    computer_session_id=uuid4().hex,
                    expires_at=pending.value.expires_at,
                )
                self._routes.require_compatible(route)
                sender = self._devices.get(route.device_id)
                if sender is None:
                    raise BridgeUnavailableError("local bridge is disconnected")
                self._routes.replace(route)
            activated = False
            try:
                result = await self._dispatch(
                    route,
                    sender,
                    ProjectSessionCommand(
                        request_id=RequestId(uuid4().hex),
                        thread_id=route.thread_id,
                        computer_session_id=route.computer_session_id,
                    ),
                )
                require_project_session_result(result)
                activated = True
            finally:
                if not activated:
                    with anyio.CancelScope(shield=True):
                        async with self._lock:
                            if self._sessions.get(session) is route:
                                self._routes.remove(route)
            return ProjectSelectionOutput(
                project_name=pending.value.project_name,
                thread_id=route.thread_id,
                expires_at=route.expires_at,
            )

    async def complete(
        self,
        device_id: DeviceId,
        sender: BridgeSender,
        result: BridgeResult,
    ) -> None:
        async with self._lock:
            if self._devices.get(device_id) is not sender:
                return
            pending = self._pending.get(result.request_id)
            if (
                pending is None
                or pending.device_id != device_id
                or pending.failure is not None
            ):
                return
            pending.result = result
            pending.event.set()

    def _disconnect_device(self, device_id: DeviceId) -> None:
        _ = self._devices.pop(device_id, None)
        stale_projects, stale_routes = disconnected_device_targets(
            self._projects,
            self._sessions.values(),
            device_id,
        )
        for scope in stale_projects:
            del self._projects[scope]
        for route in stale_routes:
            del self._sessions[route.session]
            self._routes.cancel(
                route,
                BridgeUnavailableError("selected local bridge is disconnected"),
            )
            _ = self._thread_sessions.pop(
                (route.device_id, route.thread_id),
                None,
            )

    @override
    async def _dispatch(
        self,
        route: SessionRoute,
        sender: BridgeSender,
        command: GatewayCommand,
    ) -> BridgeResult:
        pending = PendingCall(
            event=anyio.Event(),
            device_id=route.device_id,
            computer_session_id=route.computer_session_id,
        )
        async with self._lock:
            if self._sessions.get(route.session) is not route:
                raise ActiveBindingMissingError(
                    "ChatGPT session has no active project selection"
                )
            if self._devices.get(route.device_id) is not sender:
                raise BridgeUnavailableError("selected local bridge is disconnected")
            self._pending[command.request_id] = pending
        try:
            await sender.send(command)
            with anyio.fail_after(self._request_timeout_seconds):
                await pending.event.wait()
        except TimeoutError as exc:
            raise BridgeTimeoutError("local bridge response timed out") from exc
        finally:
            with anyio.CancelScope(shield=True):
                async with self._lock:
                    _ = self._pending.pop(command.request_id, None)
        if pending.failure is not None:
            raise pending.failure
        if pending.result is None:
            raise BridgeUnavailableError("local bridge returned no result")
        return pending.result

    @override
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
                raise ActiveBindingMissingError(
                    "ChatGPT session has no active project selection"
                )
            sender = self._devices.get(route.device_id)
            if sender is None:
                raise BridgeUnavailableError("selected local bridge is disconnected")
            return route, sender
