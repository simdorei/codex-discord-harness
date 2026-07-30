from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never, override

import anyio
import pytest

from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_errors import (
    ActiveBindingMissingError,
    BindingCodeError,
    BridgeUnavailableError,
    BrokerError,
)
from remote_mcp_server.simdorei_mcp.broker_models import BridgeSender
from simdorei_mcp_common.messages import (
    DeviceId,
    GatewayCommand,
    ListFilesCommand,
    ProjectInfoCommand,
    ProjectInfoOutput,
    ProjectInfoResult,
    ProjectOperationCommand,
    ProjectSessionCommand,
    ProjectSessionResult,
    ProjectUpsert,
    ReadFileCommand,
    WriteFileCommand,
)


class ProjectInfoSender(BridgeSender):
    """In-memory bridge that completes project-info commands."""

    def __init__(self, broker: BindingBroker) -> None:
        self._broker: BindingBroker = broker
        self.commands: list[ProjectInfoCommand] = []

    @override
    async def send(self, command: GatewayCommand) -> None:
        # Given
        match command:
            case ProjectInfoCommand():
                self.commands.append(command)
                result = ProjectInfoResult(
                    request_id=command.request_id,
                    output=ProjectInfoOutput(
                        root="C:/work/project-a",
                        thread_id=command.thread_id,
                    ),
                )
            case ProjectSessionCommand():
                result = ProjectSessionResult(request_id=command.request_id)
            case (
                ListFilesCommand()
                | ReadFileCommand()
                | WriteFileCommand()
                | ProjectOperationCommand()
            ):
                raise AssertionError(f"unexpected command: {command.type}")
            case _:
                assert_never(command)

        # When
        await self._broker.complete(DeviceId("device-a"), self, result)

    async def close(self) -> None:
        return None


class CancellableProjectInfoSender(ProjectInfoSender):
    def __init__(self, broker: BindingBroker) -> None:
        super().__init__(broker)
        self.project_info_started: anyio.Event = anyio.Event()
        self.release_project_info: anyio.Event = anyio.Event()

    @override
    async def send(self, command: GatewayCommand) -> None:
        if isinstance(command, ProjectInfoCommand):
            self.project_info_started.set()
            await self.release_project_info.wait()
            return
        await super().send(command)


def _project(scope: str, thread_id: str = "thread-a") -> ProjectUpsert:
    return ProjectUpsert(
        project_scope=scope,
        binding_id="binding-generation-project-a",
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
        project_scope = "codex-pro-project-a"
        await broker.upsert(DeviceId("device-a"), sender, _project(project_scope))
        _ = await broker.select("session-a", "subject-a", project_scope)

        # When
        _ = await broker.select("session-b", "subject-a", project_scope)

        # Then
        with pytest.raises(ActiveBindingMissingError):
            _ = await broker.project_info("session-a", "subject-a")
        output = await broker.project_info("session-b", "subject-a")
        assert output.thread_id == "thread-a"

    anyio.run(scenario)


def test_existing_chat_session_cannot_switch_to_another_codex_thread() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        first_scope = "codex-pro-project-a"
        second_scope = "codex-pro-project-b"
        await broker.upsert(
            DeviceId("device-a"), sender, _project(first_scope, "thread-a")
        )
        _ = await broker.select("session-a", "subject-a", first_scope)
        await broker.upsert(
            DeviceId("device-a"), sender, _project(second_scope, "thread-b")
        )

        # When / Then
        with pytest.raises(BrokerError, match="different Codex thread"):
            _ = await broker.select("session-a", "subject-a", second_scope)
        output = await broker.project_info("session-a", "subject-a")
        assert output.thread_id == "thread-a"
        rebound = await broker.select("session-b", "subject-a", second_scope)
        assert rebound.thread_id == "thread-b"

    anyio.run(scenario)


def test_device_disconnect_revokes_bound_sessions() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        project_scope = "codex-pro-project-a"
        await broker.upsert(DeviceId("device-a"), sender, _project(project_scope))
        _ = await broker.select("session-a", "subject-a", project_scope)

        # When
        await broker.detach(DeviceId("device-a"), sender)

        # Then
        with pytest.raises(ActiveBindingMissingError):
            _ = await broker.project_info("session-a", "subject-a")

    anyio.run(scenario)


def test_project_info_is_forwarded_to_bound_device() -> None:
    async def scenario() -> None:
        # Given
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        project_scope = "codex-pro-project-a"
        await broker.upsert(DeviceId("device-a"), sender, _project(project_scope))
        _ = await broker.select("session-a", "subject-a", project_scope)

        # When
        output = await broker.project_info("session-a", "subject-a")

        # Then
        assert output.root == "C:/work/project-a"
        assert len(sender.commands) == 1
        assert sender.commands[0].computer_session_id is not None

    anyio.run(scenario)


def test_cancelled_request_is_removed_from_pending_calls() -> None:
    async def scenario() -> None:
        broker = BindingBroker()
        sender = CancellableProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        project_scope = "codex-pro-project-a"
        await broker.upsert(DeviceId("device-a"), sender, _project(project_scope))
        _ = await broker.select("session-a", "subject-a", project_scope)

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(
                broker.project_info,
                "session-a",
                "subject-a",
            )
            await sender.project_info_started.wait()
            tasks.cancel_scope.cancel()

        assert broker.pending_call_count == 0

    anyio.run(scenario)


def test_fresh_registration_revokes_old_scope_and_session() -> None:
    async def scenario() -> None:
        broker = BindingBroker()
        sender = ProjectInfoSender(broker)
        await broker.attach(DeviceId("device-a"), sender)
        old_scope = "codex-pro-project-old"
        new_scope = "codex-pro-project-new"
        await broker.upsert(DeviceId("device-a"), sender, _project(old_scope))
        _ = await broker.select("session-old", "subject-a", old_scope)

        await broker.upsert(DeviceId("device-a"), sender, _project(new_scope))

        with pytest.raises(ActiveBindingMissingError):
            _ = await broker.project_info("session-old", "subject-a")
        with pytest.raises(BindingCodeError):
            _ = await broker.select("session-replay", "subject-a", old_scope)
        selected = await broker.select("session-new", "subject-a", new_scope)
        assert selected.thread_id == "thread-a"

    anyio.run(scenario)


def test_replaced_bridge_sender_cannot_restore_an_old_scope() -> None:
    async def scenario() -> None:
        broker = BindingBroker()
        stale_sender = ProjectInfoSender(broker)
        current_sender = ProjectInfoSender(broker)
        assert await broker.attach(DeviceId("device-a"), stale_sender) is None
        old_scope = "codex-pro-project-old"
        new_scope = "codex-pro-project-new"
        await broker.upsert(
            DeviceId("device-a"),
            stale_sender,
            _project(old_scope),
        )
        _ = await broker.select("session-old", "subject-a", old_scope)

        assert await broker.attach(DeviceId("device-a"), current_sender) is stale_sender
        await broker.upsert(
            DeviceId("device-a"),
            current_sender,
            _project(new_scope),
        )

        with pytest.raises(BridgeUnavailableError):
            await broker.upsert(
                DeviceId("device-a"),
                stale_sender,
                _project(old_scope),
            )
        with pytest.raises(ActiveBindingMissingError):
            _ = await broker.project_info("session-old", "subject-a")
        with pytest.raises(BindingCodeError):
            _ = await broker.select("session-replay", "subject-a", old_scope)
        selected = await broker.select("session-new", "subject-a", new_scope)
        assert selected.thread_id == "thread-a"

    anyio.run(scenario)
