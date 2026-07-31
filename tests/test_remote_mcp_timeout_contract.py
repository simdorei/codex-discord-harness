from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never, override

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
    RequestId,
    WriteFileCommand,
)
from simdorei_mcp_common.operation_outputs import CommandRunOutput
from simdorei_mcp_common.operation_requests import CommandRunRequest
from tests.remote_mcp_oauth_support import oauth_settings


class CommandRunSender(BridgeSender):
    def __init__(self, broker: BindingBroker) -> None:
        self._broker = broker
        self.command: ProjectOperationCommand | None = None

    @override
    async def send(self, command: GatewayCommand) -> None:
        match command:
            case ProjectSessionCommand():
                result = ProjectSessionResult(request_id=command.request_id)
            case ProjectOperationCommand():
                self.command = command
                result = ProjectOperationResult(
                    request_id=command.request_id,
                    output=CommandRunOutput(
                        command_id="qa",
                        exit_code=0,
                        stdout="",
                        stderr="",
                        duration_ms=1,
                        truncated=False,
                    ),
                )
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


def test_gateway_default_covers_the_longest_safe_command() -> None:
    settings = oauth_settings()

    assert settings.request_timeout_seconds >= 315


def test_command_deadline_and_external_request_id_cross_the_bridge() -> None:
    async def scenario() -> None:
        broker = BindingBroker()
        sender = CommandRunSender(broker)
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
        _ = await broker.select("session-a", "subject-a", scope)
        started = datetime.now(UTC)

        _ = await broker.project_operation(
            "session-a",
            "subject-a",
            CommandRunRequest(command_id="qa", timeout_seconds=300),
            request_id=RequestId("stable-external-request-id"),
        )

        assert sender.command is not None
        assert sender.command.request_id == "stable-external-request-id"
        assert sender.command.deadline_at >= started + timedelta(seconds=315)

    anyio.run(scenario)
