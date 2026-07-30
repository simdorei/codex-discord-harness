from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never

import anyio

from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_models import BridgeSender
from simdorei_mcp_common.messages import (
    DeviceId,
    GatewayCommand,
    ListFilesCommand,
    ProjectInfoCommand,
    ProjectOperationCommand,
    ProjectOperationResult,
    ProjectSessionCommand,
    ProjectSessionResult,
    ProjectUpsert,
    ReadFileCommand,
    WriteFileCommand,
)
from simdorei_mcp_common.operation_outputs import RepoStatusOutput
from simdorei_mcp_common.operation_requests import RepoStatusRequest


class OperationSender(BridgeSender):
    def __init__(self, broker: BindingBroker) -> None:
        self._broker = broker
        self.command: ProjectOperationCommand | None = None
        self.commands: list[ProjectOperationCommand] = []

    async def send(self, command: GatewayCommand) -> None:
        match command:
            case ProjectOperationCommand():
                self.command = command
                self.commands.append(command)
                result = ProjectOperationResult(
                    request_id=command.request_id,
                    output=RepoStatusOutput(
                        branch="main",
                        dirty_files=(),
                        staged_files=(),
                        remotes=("origin",),
                        upstream="origin/main",
                        ahead=0,
                        behind=0,
                    ),
                )
            case ProjectSessionCommand():
                result = ProjectSessionResult(request_id=command.request_id)
            case (
                ProjectInfoCommand()
                | ListFilesCommand()
                | ReadFileCommand()
                | WriteFileCommand()
            ):
                raise AssertionError(f"unexpected command: {command.type}")
            case unreachable:
                assert_never(unreachable)
        await self._broker.complete(DeviceId("device-a"), self, result)

    async def close(self) -> None:
        return None


def test_project_operation_is_forwarded_to_selected_local_thread() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = OperationSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        scope = "codex-pro-project-a"
        await broker.upsert(
            DeviceId("device-a"),
            sender,
            ProjectUpsert(
                project_scope=scope,
                binding_id="binding-generation-project-a",
                thread_id="thread-a",
                project_name="project-a",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ),
        )
        await broker.select("session-a", "subject-a", scope)

        # When
        output = await broker.project_operation(
            "session-a",
            "subject-a",
            RepoStatusRequest(),
        )

        # Then
        assert isinstance(output, RepoStatusOutput)
        assert sender.command is not None
        assert sender.command.thread_id == "thread-a"
        assert sender.command.computer_session_id is not None

    anyio.run(scenario)


def test_new_chat_owner_gets_a_new_computer_session_generation() -> None:
    async def scenario() -> None:
        broker = BindingBroker()
        sender = OperationSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        scope = "codex-pro-project-a"
        await broker.upsert(
            DeviceId("device-a"),
            sender,
            ProjectUpsert(
                project_scope=scope,
                binding_id="binding-generation-project-a",
                thread_id="thread-a",
                project_name="project-a",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ),
        )
        await broker.select("session-a", "subject-a", scope)
        _ = await broker.project_operation(
            "session-a",
            "subject-a",
            RepoStatusRequest(),
        )

        await broker.select("session-b", "subject-a", scope)
        _ = await broker.project_operation(
            "session-b",
            "subject-a",
            RepoStatusRequest(),
        )

        generations = [command.computer_session_id for command in sender.commands]
        assert None not in generations
        assert generations[0] != generations[1]

    anyio.run(scenario)
