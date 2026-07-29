from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import assert_never

from fastapi.testclient import TestClient
from pydantic import SecretStr

from remote_mcp_server.simdorei_mcp.app import create_app
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from simdorei_mcp_common.messages import (
    BindingAck,
    BindingUpsert,
    BridgeHello,
    GatewayHello,
    ListFilesCommand,
    ProjectInfoCommand,
    ReadFileCommand,
    WriteFileCommand,
    parse_gateway_message,
)


def _settings() -> GatewaySettings:
    return GatewaySettings(
        device_id="device-a",
        device_token=SecretStr("bridge-secret-1234567890"),
        public_base_url="https://simdorei.duckdns.org",
    )


def test_bridge_accepts_authenticated_binding() -> None:
    # Given
    app = create_app(_settings())
    headers = {"Authorization": "Bearer bridge-secret-1234567890"}

    # When
    with (
        TestClient(app) as client,
        client.websocket_connect("/bridge", headers=headers) as socket,
    ):
        socket.send_text(BridgeHello(device_id="device-a").model_dump_json())
        hello = parse_gateway_message(socket.receive_text())
        socket.send_text(
            BindingUpsert(
                binding_code="binding-code-123456789012",
                thread_id="thread-a",
                project_name="project-a",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ).model_dump_json()
        )
        binding = parse_gateway_message(socket.receive_text())

    # Then
    match hello:
        case GatewayHello():
            pass
        case BindingAck() | ProjectInfoCommand() | ListFilesCommand() | ReadFileCommand() | WriteFileCommand():
            raise AssertionError(f"unexpected message: {hello.type}")
        case unreachable:
            assert_never(unreachable)
    match binding:
        case BindingAck():
            pass
        case GatewayHello() | ProjectInfoCommand() | ListFilesCommand() | ReadFileCommand() | WriteFileCommand():
            raise AssertionError(f"unexpected message: {binding.type}")
        case unreachable:
            assert_never(unreachable)


def test_mcp_initialize_and_tool_listing() -> None:
    # Given
    app = create_app(_settings())
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "integration-test", "version": "1"},
        },
    }

    # When
    with TestClient(app, base_url="http://localhost") as client:
        initialized = client.post("/mcp", headers=headers, json=initialize)
        tools = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    # Then
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "simdorei-local-project"
    assert tools.status_code == 200
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert names == {
        "bind_project",
        "list_project_files",
        "project_info",
        "read_project_file",
        "write_project_file",
    }
