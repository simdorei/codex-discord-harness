from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never

import anyio
import pytest

from remote_mcp_server.simdorei_mcp.broker import BindingBroker, BridgeSender
from remote_mcp_server.simdorei_mcp.broker_errors import (
    ActiveBindingMissingError,
    BrokerError,
)
from simdorei_mcp_common.messages import (
    BindingUpsert,
    DeviceId,
    GatewayCommand,
    ListFilesCommand,
    ProjectInfoCommand,
    ProjectInfoOutput,
    ProjectInfoResult,
    ReadFileCommand,
    WriteFileCommand,
)


class ProjectInfoSender(BridgeSender):
    """In-memory bridge that completes project-info commands."""

    def __init__(self, broker: BindingBroker) -> None:
        self._broker = broker
        self.commands: list[ProjectInfoCommand] = []

    async def send(self, command: GatewayCommand) -> None:
        # Given
        match command:
            case ProjectInfoCommand():
                self.commands.append(command)
            case ListFilesCommand() | ReadFileCommand() | WriteFileCommand():
                raise AssertionError(f"unexpected command: {command.type}")
            case unreachable:
                assert_never(unreachable)

        # When
        await self._broker.complete(
            DeviceId("device-a"),
            ProjectInfoResult(
                request_id=command.request_id,
                output=ProjectInfoOutput(
                    root="C:/work/project-a",
                    thread_id=command.thread_id,
                ),
            ),
        )


def _binding(code: str, thread_id: str = "thread-a") -> BindingUpsert:
    return BindingUpsert(
        binding_code=code,
        thread_id=thread_id,
        project_name="project-a",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )


def test_new_chat_session_revokes_previous_session_for_thread() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        first_code = "first-binding-code-123456"
        second_code = "second-binding-code-12345"
        await broker.upsert(DeviceId("device-a"), _binding(first_code))
        await broker.bind("session-a", "subject-a", first_code)
        await broker.upsert(DeviceId("device-a"), _binding(second_code))

        # When
        await broker.bind("session-b", "subject-a", second_code)

        # Then
        with pytest.raises(ActiveBindingMissingError):
            await broker.project_info("session-a", "subject-a")
        output = await broker.project_info("session-b", "subject-a")
        assert output.thread_id == "thread-a"

    anyio.run(scenario)


def test_existing_chat_session_cannot_switch_to_another_codex_thread() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        first_code = "first-binding-code-123456"
        second_code = "second-binding-code-12345"
        await broker.upsert(DeviceId("device-a"), _binding(first_code, "thread-a"))
        await broker.bind("session-a", "subject-a", first_code)
        await broker.upsert(DeviceId("device-a"), _binding(second_code, "thread-b"))

        # When / Then
        with pytest.raises(BrokerError, match="different Codex thread"):
            await broker.bind("session-a", "subject-a", second_code)
        output = await broker.project_info("session-a", "subject-a")
        assert output.thread_id == "thread-a"
        rebound = await broker.bind("session-b", "subject-a", second_code)
        assert rebound.thread_id == "thread-b"

    anyio.run(scenario)


def test_device_disconnect_revokes_bound_sessions() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        binding_code = "binding-code-123456789012"
        await broker.upsert(DeviceId("device-a"), _binding(binding_code))
        await broker.bind("session-a", "subject-a", binding_code)

        # When
        await broker.detach(DeviceId("device-a"), sender)

        # Then
        with pytest.raises(ActiveBindingMissingError):
            await broker.project_info("session-a", "subject-a")

    anyio.run(scenario)


def test_project_info_is_forwarded_to_bound_device() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        binding_code = "binding-code-123456789012"
        await broker.upsert(DeviceId("device-a"), _binding(binding_code))
        await broker.bind("session-a", "subject-a", binding_code)

        # When
        output = await broker.project_info("session-a", "subject-a")

        # Then
        assert output.root == "C:/work/project-a"
        assert len(sender.commands) == 1

    anyio.run(scenario)
