from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pydantic import SecretStr

from remote_mcp_server.simdorei_mcp.app import create_app
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from simdorei_mcp_common.messages import (
    BridgeHello,
    ProjectUpsert,
    parse_gateway_message,
)


def _settings(oauth_database_path: Path | None = None) -> GatewaySettings:
    return GatewaySettings(
        device_id="device-a",
        device_token=SecretStr("bridge-secret-1234567890"),
        public_base_url="https://simdorei.duckdns.org",
        owner_token=SecretStr("owner-secret-12345678901234567890"),
        oauth_database_path=oauth_database_path or Path(":memory:"),
    )


def test_mcp_initialize_and_tool_listing() -> None:
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

    with TestClient(app, base_url="http://localhost") as client:
        access_token = _authorize(client)
        authorized_headers = {
            **headers,
            "Authorization": f"Bearer {access_token}",
        }
        initialized = client.post(
            "/mcp",
            headers=authorized_headers,
            json=initialize,
        )
        tools = client.post(
            "/mcp",
            headers=authorized_headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )

    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "simdorei-local-project"
    assert tools.status_code == 200
    names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert names == {
        "checkpoint_list",
        "checkpoint_restore",
        "checkpoint_show",
        "code_search",
        "command_list",
        "command_run",
        "file_apply_patch",
        "file_create",
        "file_read_slice",
        "git_commit",
        "git_push",
        "list_images",
        "list_project_files",
        "project_info",
        "project_rules",
        "project_status",
        "read_project_file",
        "repo_diff_summary",
        "repo_status",
        "retrieve_image",
        "save_image",
        "save_image_from_url",
        "select_project",
        "show_changes",
        "write_project_file",
    }


def test_mcp_requires_oauth_and_publishes_discovery(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path / "oauth.sqlite3"))
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

    with TestClient(app, base_url="http://localhost") as client:
        protected = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=initialize,
        )
        resource_metadata = client.get(
            "/.well-known/oauth-protected-resource/mcp"
        )
        server_metadata = client.get("/.well-known/oauth-authorization-server")

    assert protected.status_code == 401
    assert resource_metadata.status_code == 200
    assert resource_metadata.json()["resource"] == "https://simdorei.duckdns.org/mcp"
    assert server_metadata.status_code == 200
    assert server_metadata.json()["registration_endpoint"] == (
        "https://simdorei.duckdns.org/register"
    )


def test_oauth_chat_session_selects_registered_local_project(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path / "oauth.sqlite3"))
    bridge_headers = {"Authorization": "Bearer bridge-secret-1234567890"}
    mcp_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

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
            selected = client.post(
                "/mcp",
                headers={
                    **mcp_headers,
                    "Authorization": f"Bearer {access_token}",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "select_project",
                        "arguments": {"project_scope": "codex-pro-project-a"},
                        "_meta": {
                            "openai/session": "chat-session-a",
                            "openai/subject": "untrusted-subject",
                        },
                    },
                },
            )

    assert selected.status_code == 200, selected.text
    result = selected.json()["result"]
    assert result["structuredContent"]["thread_id"] == "thread-a"
    assert result["isError"] is False


def test_oauth_access_token_survives_gateway_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "oauth.sqlite3"
    with TestClient(
        create_app(_settings(database_path)),
        base_url="http://localhost",
    ) as first_client:
        access_token = _authorize(first_client)

    with TestClient(
        create_app(_settings(database_path)),
        base_url="http://localhost",
    ) as restarted_client:
        listed = restarted_client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
        )

    assert listed.status_code == 200, listed.text
    assert listed.json()["result"]["tools"]


def _authorize(client: TestClient) -> str:
    redirect_uri = "https://chatgpt.com/connector/oauth/test"
    registered = client.post(
        "/register",
        json={
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "files:read files:write",
            "client_name": "ChatGPT integration test",
        },
    )
    assert registered.status_code == 201, registered.text
    client_info = registered.json()
    verifier = "oauth-pkce-verifier-123456789012345678901234567890"
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("utf-8")).digest()
    ).decode("ascii").rstrip("=")
    authorization = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_info["client_id"],
            "redirect_uri": redirect_uri,
            "scope": "files:read files:write",
            "state": "integration-state",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": "https://simdorei.duckdns.org/mcp",
        },
        follow_redirects=False,
    )
    assert authorization.status_code == 302, authorization.text
    request_id = parse_qs(urlparse(authorization.headers["location"]).query)[
        "request_id"
    ][0]
    approval_page = client.get("/oauth/approve", params={"request_id": request_id})
    assert approval_page.status_code == 200
    approved = client.post(
        "/oauth/approve",
        data={
            "request_id": request_id,
            "owner_token": "owner-secret-12345678901234567890",
        },
        follow_redirects=False,
    )
    assert approved.status_code == 302, approved.text
    callback_query = parse_qs(urlparse(approved.headers["location"]).query)
    assert callback_query["state"] == ["integration-state"]
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": callback_query["code"][0],
            "client_id": client_info["client_id"],
            "client_secret": client_info["client_secret"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": "https://simdorei.duckdns.org/mcp",
        },
    )
    assert token.status_code == 200, token.text
    return str(token.json()["access_token"])
