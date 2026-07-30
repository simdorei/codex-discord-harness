from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from remote_mcp_server.simdorei_mcp.app import create_app
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from simdorei_mcp_common.messages import (
    BridgeHello,
    GatewayHello,
    ListFilesCommand,
    ProjectAck,
    ProjectInfoCommand,
    ProjectUpsert,
    ReadFileCommand,
    WriteFileCommand,
    parse_gateway_message,
)


def _settings() -> GatewaySettings:
    return GatewaySettings(
        device_id="device-a",
        device_token=SecretStr("bridge-secret-1234567890"),
        public_base_url="https://simdorei.duckdns.org",
        owner_token=SecretStr("owner-secret-12345678901234567890"),
    )


def test_bridge_accepts_authenticated_project_registration() -> None:
    app = create_app(_settings())
    headers = {"Authorization": "Bearer bridge-secret-1234567890"}

    with (
        TestClient(app) as client,
        client.websocket_connect("/bridge", headers=headers) as socket,
    ):
        socket.send_text(
            BridgeHello(
                protocol_version=2,
                device_id="device-a",
            ).model_dump_json()
        )
        hello = parse_gateway_message(socket.receive_text())
        socket.send_text(
            ProjectUpsert(
                project_scope="codex-pro-project-a",
                thread_id="thread-a",
                project_name="project-a",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ).model_dump_json()
        )
        project = parse_gateway_message(socket.receive_text())

    match hello:
        case GatewayHello():
            pass
        case ProjectAck() | ProjectInfoCommand() | ListFilesCommand() | ReadFileCommand() | WriteFileCommand():
            raise AssertionError(f"unexpected message: {hello.type}")
        case unreachable:
            assert_never(unreachable)
    match project:
        case ProjectAck():
            pass
        case GatewayHello() | ProjectInfoCommand() | ListFilesCommand() | ReadFileCommand() | WriteFileCommand():
            raise AssertionError(f"unexpected message: {project.type}")
        case unreachable:
            assert_never(unreachable)


def test_bridge_protocol_rejects_version_one_hello() -> None:
    with pytest.raises(ValidationError):
        BridgeHello.model_validate(
            {
                "type": "hello",
                "protocol_version": 1,
                "device_id": "device-a",
            }
        )
