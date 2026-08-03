from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

import remote_mcp_server.simdorei_mcp.app as app_module
from remote_mcp_server.simdorei_mcp.app import create_app
from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    CAPABILITY_GROUPS,
    CapabilityGroup,
    CapabilityInventoryOutput,
    CapabilitySurface,
    ToolInventoryMismatch,
    find_duplicate_tool_names,
    require_complete_tool_inventory,
)
from remote_mcp_server.simdorei_mcp.tools import register_tools
from tests.remote_mcp_oauth_support import authorize, oauth_settings

EXPECTED_TOOL_NAMES = (
    "activate_computer_window",
    "capability_inventory",
    "checkpoint_list",
    "checkpoint_restore",
    "checkpoint_show",
    "click_computer_window",
    "close_computer_window",
    "code_search",
    "command_list",
    "command_run",
    "drag_computer_window",
    "file_apply_patch",
    "file_create",
    "file_read_slice",
    "git_commit",
    "git_push",
    "launch_computer_app",
    "list_computer_windows",
    "list_images",
    "list_project_files",
    "press_computer_keys",
    "project_info",
    "project_rules",
    "project_status",
    "read_project_file",
    "repo_diff_summary",
    "repo_status",
    "retrieve_image",
    "save_image",
    "save_image_from_url",
    "screenshot_computer_window",
    "scroll_computer_window",
    "select_project",
    "set_computer_clipboard",
    "show_changes",
    "stop_computer_control",
    "terminal_exec",
    "terminal_window_activate",
    "terminal_window_capture",
    "terminal_window_close",
    "terminal_window_interrupt",
    "terminal_window_keys",
    "terminal_window_list",
    "terminal_window_open",
    "terminal_window_type",
    "type_computer_text",
    "write_project_file",
)
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class _SecurityScheme(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    scopes: tuple[str, ...]


class _ToolMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    security_schemes: tuple[_SecurityScheme, ...] = Field(alias="securitySchemes")


class _ListedTool(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    name: str
    metadata: _ToolMetadata = Field(alias="_meta")


class _ToolListResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    tools: tuple[_ListedTool, ...]


class _ToolListEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    result: _ToolListResult


class _ToolCallResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    is_error: bool = Field(alias="isError")
    structured_content: CapabilityInventoryOutput = Field(alias="structuredContent")


class _ToolCallEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    result: _ToolCallResult


def test_mcp_registered_tool_inventory_matches_release_manifest() -> None:
    app = create_app(oauth_settings())

    with TestClient(app, base_url="http://localhost") as client:
        token = authorize(client)
        response = client.post(
            "/mcp",
            headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert response.status_code == 200, response.text
    envelope = _ToolListEnvelope.model_validate_json(response.content)
    actual_names = tuple(sorted(tool.name for tool in envelope.result.tools))
    assert actual_names == EXPECTED_TOOL_NAMES
    manifest_scopes = {
        tool_name: group.oauth_scopes
        for group in CAPABILITY_GROUPS
        for tool_name in group.tools
    }
    for tool in envelope.result.tools:
        assert len(tool.metadata.security_schemes) == 1
        assert tool.metadata.security_schemes[0].scopes == manifest_scopes[tool.name]


def test_capability_inventory_tool_reports_grouped_runtime_registration() -> None:
    app = create_app(oauth_settings())

    with TestClient(app, base_url="http://localhost") as client:
        token = authorize(client)
        response = client.post(
            "/mcp",
            headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "capability_inventory",
                    "arguments": {},
                    "_meta": {"openai/session": "inventory-session-a"},
                },
            },
        )

    assert response.status_code == 200, response.text
    envelope = _ToolCallEnvelope.model_validate_json(response.content)
    assert envelope.result.is_error is False
    inventory = envelope.result.structured_content
    assert inventory.ready is True
    assert inventory.expected_tool_count == len(EXPECTED_TOOL_NAMES)
    assert inventory.registered_tool_count == len(EXPECTED_TOOL_NAMES)
    assert inventory.manifest_duplicate_tools == ()
    groups: dict[CapabilitySurface, CapabilityGroup] = {
        group.surface: group for group in inventory.groups
    }
    assert set(groups) == {
        CapabilitySurface.READ,
        CapabilitySurface.WRITE,
        CapabilitySurface.GIT,
        CapabilitySurface.TERMINAL_EXECUTE,
        CapabilitySurface.TERMINAL_INTERACT,
        CapabilitySurface.COMPUTER_OBSERVE,
        CapabilitySurface.COMPUTER_CONTROL,
    }
    assert groups[CapabilitySurface.GIT].tools == ("git_commit", "git_push")
    assert groups[CapabilitySurface.WRITE].oauth_scopes == (
        "files:read",
        "files:write",
    )
    assert groups[CapabilitySurface.GIT].oauth_scopes == (
        "files:read",
        "files:write",
    )
    assert groups[CapabilitySurface.TERMINAL_EXECUTE].oauth_scopes == (
        "files:read",
        "files:write",
        "terminal:execute",
    )
    assert groups[CapabilitySurface.TERMINAL_EXECUTE].tools == (
        "terminal_exec",
        "terminal_window_close",
        "terminal_window_list",
        "terminal_window_open",
    )
    assert groups[CapabilitySurface.TERMINAL_INTERACT].oauth_scopes == (
        "files:read",
        "files:write",
        "terminal:execute",
        "terminal:interact",
    )
    assert groups[CapabilitySurface.TERMINAL_INTERACT].tools == (
        "terminal_window_activate",
        "terminal_window_capture",
        "terminal_window_interrupt",
        "terminal_window_keys",
        "terminal_window_type",
    )
    assert "write_project_file" in groups[CapabilitySurface.WRITE].tools
    assert "type_computer_text" in groups[CapabilitySurface.COMPUTER_CONTROL].tools


def test_release_blocker_rejects_missing_and_unexpected_tools() -> None:
    drifted: set[str] = set(EXPECTED_TOOL_NAMES)
    drifted.remove("git_push")
    drifted.add("unreviewed_tool")

    with pytest.raises(
        ToolInventoryMismatch,
        match=(
            r"MCP tool inventory mismatch: "
            r"missing=\['git_push'\], unexpected=\['unreviewed_tool'\], "
            r"manifest_duplicates=\[\]"
        ),
    ):
        _ = require_complete_tool_inventory(drifted)


def test_manifest_duplicate_detection_is_independent_of_registered_names() -> None:
    groups = (
        CapabilityGroup(
            surface=CapabilitySurface.READ,
            oauth_scopes=("files:read",),
            tools=("shared_tool", "read_tool"),
        ),
        CapabilityGroup(
            surface=CapabilitySurface.WRITE,
            oauth_scopes=("files:read", "files:write"),
            tools=("shared_tool", "write_tool"),
        ),
    )

    assert find_duplicate_tool_names(groups) == ("shared_tool",)


def test_create_app_fails_closed_after_registered_inventory_drifts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def register_without_git_push(mcp: FastMCP, broker: BindingBroker) -> None:
        register_tools(mcp, broker)
        mcp.remove_tool("git_push")

    monkeypatch.setattr(app_module, "register_tools", register_without_git_push)

    with pytest.raises(
        ToolInventoryMismatch,
        match=r"MCP tool inventory mismatch: missing=\['git_push'\]",
    ):
        _ = app_module.create_app(oauth_settings())
