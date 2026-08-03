from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from remote_mcp_server.simdorei_mcp.broker_models import BridgeSender, SessionRoute
from remote_mcp_server.simdorei_mcp.broker_results import (
    list_files_output,
    operation_output,
    project_info_output,
    read_file_output,
    write_file_output,
)
from simdorei_mcp_common.messages import (
    BridgeResult,
    GatewayCommand,
    ListFilesCommand,
    ListFilesOutput,
    ProjectInfoCommand,
    ProjectInfoOutput,
    ProjectOperationCommand,
    ReadFileCommand,
    ReadFileOutput,
    RequestId,
    WriteFileCommand,
    WriteFileOutput,
)
from simdorei_mcp_common.operation_outputs import ProjectOperationOutput
from simdorei_mcp_common.operation_requests import ProjectOperation
from simdorei_mcp_common.request_deadlines import operation_request_deadline
from simdorei_mcp_common.runtime_provenance import (
    RuntimeProvenanceEnvelope,
    runtime_session_binding_sha256,
)


class BrokerTransport(Protocol):
    async def _route(
        self,
        session: str,
        subject: str,
    ) -> tuple[SessionRoute, BridgeSender]: ...

    async def _dispatch(
        self,
        route: SessionRoute,
        sender: BridgeSender,
        command: GatewayCommand,
    ) -> BridgeResult: ...

    async def _runtime_cycle_binding(self, route: SessionRoute) -> str | None: ...


class BrokerRequestsMixin:
    """Public project-operation facade shared by the synchronized broker."""

    async def _route(
        self,
        session: str,
        subject: str,
    ) -> tuple[SessionRoute, BridgeSender]:
        raise NotImplementedError

    async def _dispatch(
        self,
        route: SessionRoute,
        sender: BridgeSender,
        command: GatewayCommand,
    ) -> BridgeResult:
        raise NotImplementedError

    async def _runtime_cycle_binding(self, route: SessionRoute) -> str | None:
        raise NotImplementedError

    def _transport(self) -> BrokerTransport:
        return self

    async def project_info(self, session: str, subject: str) -> ProjectInfoOutput:
        return await project_info(self._transport(), session, subject)

    async def list_files(
        self,
        session: str,
        subject: str,
        *,
        pattern: str,
        limit: int,
    ) -> ListFilesOutput:
        return await list_files(
            self._transport(),
            session,
            subject,
            pattern=pattern,
            limit=limit,
        )

    async def read_file(
        self,
        session: str,
        subject: str,
        command: ReadFileCommand,
    ) -> ReadFileOutput:
        return await read_file(self._transport(), session, subject, command)

    async def write_file(
        self,
        session: str,
        subject: str,
        command: WriteFileCommand,
    ) -> WriteFileOutput:
        return await write_file(self._transport(), session, subject, command)

    async def project_operation(
        self,
        session: str,
        subject: str,
        operation: ProjectOperation,
        *,
        request_id: RequestId | None = None,
    ) -> ProjectOperationOutput:
        return await project_operation(
            self._transport(),
            session,
            subject,
            operation,
            request_id=request_id,
        )


async def project_info(
    broker: BrokerTransport,
    session: str,
    subject: str,
) -> ProjectInfoOutput:
    route, sender = await broker._route(session, subject)
    result = await broker._dispatch(
        route,
        sender,
        ProjectInfoCommand(
            request_id=RequestId(uuid4().hex),
            thread_id=route.thread_id,
            computer_session_id=route.computer_session_id,
        ),
    )
    return project_info_output(result)


async def list_files(
    broker: BrokerTransport,
    session: str,
    subject: str,
    *,
    pattern: str,
    limit: int,
) -> ListFilesOutput:
    route, sender = await broker._route(session, subject)
    result = await broker._dispatch(
        route,
        sender,
        ListFilesCommand(
            request_id=RequestId(uuid4().hex),
            thread_id=route.thread_id,
            computer_session_id=route.computer_session_id,
            pattern=pattern,
            limit=limit,
        ),
    )
    return list_files_output(result)


async def read_file(
    broker: BrokerTransport,
    session: str,
    subject: str,
    command: ReadFileCommand,
) -> ReadFileOutput:
    route, sender = await broker._route(session, subject)
    routed = command.model_copy(
        update={
            "thread_id": route.thread_id,
            "computer_session_id": route.computer_session_id,
        }
    )
    return read_file_output(await broker._dispatch(route, sender, routed))


async def write_file(
    broker: BrokerTransport,
    session: str,
    subject: str,
    command: WriteFileCommand,
) -> WriteFileOutput:
    route, sender = await broker._route(session, subject)
    routed = command.model_copy(
        update={
            "thread_id": route.thread_id,
            "computer_session_id": route.computer_session_id,
        }
    )
    return write_file_output(await broker._dispatch(route, sender, routed))


async def project_operation(
    broker: BrokerTransport,
    session: str,
    subject: str,
    operation: ProjectOperation,
    *,
    request_id: RequestId | None = None,
) -> ProjectOperationOutput:
    route, sender = await broker._route(session, subject)
    session_binding = runtime_session_binding_sha256(session, subject)
    cycle_binding = await broker._runtime_cycle_binding(route)
    result = await broker._dispatch(
        route,
        sender,
        ProjectOperationCommand(
            request_id=request_id or RequestId(uuid4().hex),
            thread_id=route.thread_id,
            computer_session_id=route.computer_session_id,
            runtime_provenance=RuntimeProvenanceEnvelope(
                session_binding_sha256=session_binding,
                cycle_binding_sha256=cycle_binding,
            ),
            deadline_at=operation_request_deadline(operation),
            operation=operation,
        ),
    )
    return operation_output(result)
