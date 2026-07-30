from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from remote_mcp_server.simdorei_mcp.app import create_app
from simdorei_mcp_common.messages import (
    BridgeHello,
    ProjectOperationCommand,
    ProjectOperationResult,
    ProjectUpsert,
    parse_gateway_message,
)
from simdorei_mcp_common.operation_outputs import (
    ImageEntry,
    ImageRetrieveOutput,
)
from simdorei_mcp_common.operation_requests import RetrieveImageRequest
from tests.test_remote_mcp_oauth import _authorize, _settings

PNG_BYTES = b"\x89PNG\r\n\x1a\nroundtrip-image"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def test_retrieve_image_returns_native_mcp_image_content() -> None:
    # Given
    app = create_app(_settings())
    bridge_headers = {"Authorization": "Bearer bridge-secret-1234567890"}
    with TestClient(app, base_url="http://localhost") as client:  # noqa: SIM117
        with client.websocket_connect("/bridge", headers=bridge_headers) as socket:
            socket.send_text(
                BridgeHello(
                    protocol_version=2,
                    device_id="device-a",
                ).model_dump_json()
            )
            _ = parse_gateway_message(socket.receive_text())
            socket.send_text(
                ProjectUpsert(
                    project_scope="codex-pro-project-a",
                    thread_id="thread-a",
                    project_name="project-a",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                ).model_dump_json()
            )
            _ = parse_gateway_message(socket.receive_text())
            access_token = _authorize(client)
            headers = {
                **MCP_HEADERS,
                "Authorization": f"Bearer {access_token}",
            }
            _select_project(client, headers)

            # When
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.post,
                    "/mcp",
                    headers=headers,
                    json=_retrieve_call(),
                )
                command = parse_gateway_message(socket.receive_text())
                assert isinstance(command, ProjectOperationCommand)
                assert isinstance(command.operation, RetrieveImageRequest)
                socket.send_text(
                    ProjectOperationResult(
                        request_id=command.request_id,
                        output=ImageRetrieveOutput(
                            image=ImageEntry(
                                path="assets/test.png",
                                media_type="image/png",
                                size_bytes=len(PNG_BYTES),
                            ),
                            data_base64=base64.b64encode(PNG_BYTES).decode("ascii"),
                        ),
                    ).model_dump_json()
                )
                response = pending.result(timeout=5)

    # Then
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["content"][0]["type"] == "image"
    assert result["content"][0]["mimeType"] == "image/png"
    assert base64.b64decode(result["content"][0]["data"]) == PNG_BYTES


def _select_project(client: TestClient, headers: dict[str, str]) -> None:
    selected = client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "select_project",
                "arguments": {"project_scope": "codex-pro-project-a"},
                "_meta": {"openai/session": "chat-session-a"},
            },
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["result"]["isError"] is False


def _retrieve_call() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "retrieve_image",
            "arguments": {"path": "assets/test.png"},
            "_meta": {"openai/session": "chat-session-a"},
        },
    }
