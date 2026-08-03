from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    CapabilityInventoryOutput,
    build_capability_inventory,
    capability_inventory_sha256,
)
from remote_mcp_server.simdorei_mcp.oauth_scopes import READ_SCOPE, WRITE_SCOPE
from remote_mcp_server.simdorei_mcp.tool_context import (
    READ_AUTH_META,
    READ_ONLY_ANNOTATIONS,
    SELECT_ANNOTATIONS,
    WRITE_ANNOTATIONS,
    WRITE_AUTH_META,
    ToolContext,
    bind_tool_request_id,
    tool_identity,
)
from simdorei_mcp_common.messages import (
    ListFilesOutput,
    ProjectInfoOutput,
    ProjectSelectionOutput,
    ReadFileCommand,
    ReadFileOutput,
    RequestId,
    WriteFileCommand,
    WriteFileOutput,
)


def register_tools(mcp: FastMCP, broker: BindingBroker) -> None:
    @mcp.tool(
        title="Show MCP capability inventory",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=READ_AUTH_META,
        structured_output=True,
    )
    async def capability_inventory(  # pyright: ignore[reportUnusedFunction]
        ctx: ToolContext,
    ) -> CapabilityInventoryOutput:
        """Compare the release manifest with tools registered by this server."""
        identity = tool_identity(ctx, READ_SCOPE)
        registered = registered_tool_names(mcp)
        inventory = build_capability_inventory(registered)
        tool_count = inventory.registered_tool_count
        terminal_execute_present = "terminal_exec" in registered
        terminal_interact_present = all(
            name in registered
            for name in (
                "terminal_window_capture",
                "terminal_window_type",
                "terminal_window_keys",
                "terminal_window_interrupt",
            )
        )
        if (
            tool_count != 47
            or not terminal_execute_present
            or not terminal_interact_present
        ):
            raise ToolError("The MCP capability inventory is incomplete.")
        try:
            _ = await broker.observe_runtime_capability(
                identity.session,
                identity.subject,
                inventory_sha256=capability_inventory_sha256(inventory),
                tool_count=tool_count,
                terminal_execute_present=terminal_execute_present,
                terminal_interact_present=terminal_interact_present,
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc
        return inventory

    @mcp.tool(
        title="Select local project",
        annotations=SELECT_ANNOTATIONS,
        meta=READ_AUTH_META,
        structured_output=True,
    )
    async def select_project(  # pyright: ignore[reportUnusedFunction]
        project_scope: str,
        ctx: ToolContext,
    ) -> ProjectSelectionOutput:
        """Select the active Codex project registered for this ChatGPT conversation."""
        identity = tool_identity(ctx, READ_SCOPE)
        try:
            return await broker.select(
                identity.session,
                identity.subject,
                project_scope,
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Show bound project",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=READ_AUTH_META,
        structured_output=True,
    )
    async def project_info(  # pyright: ignore[reportUnusedFunction]
        ctx: ToolContext,
    ) -> ProjectInfoOutput:
        """Show the project root and Codex thread bound to this conversation."""
        identity = tool_identity(ctx, READ_SCOPE)
        try:
            return await broker.project_info(identity.session, identity.subject)
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="List project files",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=READ_AUTH_META,
        structured_output=True,
    )
    async def list_project_files(  # pyright: ignore[reportUnusedFunction]
        ctx: ToolContext,
        pattern: str = "**/*",
        limit: int = 200,
    ) -> ListFilesOutput:
        """List non-sensitive files inside the bound project."""
        identity = tool_identity(ctx, READ_SCOPE)
        try:
            return await broker.list_files(
                identity.session,
                identity.subject,
                pattern=pattern,
                limit=limit,
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Read project file",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=READ_AUTH_META,
        structured_output=True,
    )
    async def read_project_file(  # pyright: ignore[reportUnusedFunction]
        ctx: ToolContext,
        path: str,
        start_line: int = 1,
        max_lines: int = 250,
    ) -> ReadFileOutput:
        """Read a bounded UTF-8 section from a non-sensitive project file."""
        identity = tool_identity(ctx, READ_SCOPE)
        command = bind_tool_request_id(
            ctx,
            identity,
            ReadFileCommand(
                request_id=RequestId("pending-tool-request"),
                thread_id="pending-route",
                path=path,
                start_line=start_line,
                max_lines=max_lines,
            ),
        )
        try:
            return await broker.read_file(
                identity.session,
                identity.subject,
                command,
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Write project file",
        annotations=WRITE_ANNOTATIONS,
        meta=WRITE_AUTH_META,
        structured_output=True,
    )
    async def write_project_file(  # pyright: ignore[reportUnusedFunction]
        ctx: ToolContext,
        path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> WriteFileOutput:
        """Write UTF-8 text with optimistic conflict protection inside the bound project."""
        identity = tool_identity(ctx, WRITE_SCOPE)
        command = bind_tool_request_id(
            ctx,
            identity,
            WriteFileCommand(
                request_id=RequestId("pending-tool-request"),
                thread_id="pending-route",
                path=path,
                content=content,
                expected_sha256=expected_sha256,
            ),
        )
        try:
            return await broker.write_file(
                identity.session,
                identity.subject,
                command,
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    from remote_mcp_server.simdorei_mcp.tools_checkpoints import (
        register_checkpoint_tools,
    )
    from remote_mcp_server.simdorei_mcp.tools_commands_git import (
        register_command_git_tools,
    )
    from remote_mcp_server.simdorei_mcp.tools_computer import register_computer_tools
    from remote_mcp_server.simdorei_mcp.tools_images import register_image_tools
    from remote_mcp_server.simdorei_mcp.tools_project import register_project_tools
    from remote_mcp_server.simdorei_mcp.tools_terminal import register_terminal_tools
    from remote_mcp_server.simdorei_mcp.tools_terminal_windows import (
        register_terminal_window_tools,
    )
    from remote_mcp_server.simdorei_mcp.tools_terminal_window_interactions import (
        register_terminal_window_interaction_tools,
    )

    register_project_tools(mcp, broker)
    register_command_git_tools(mcp, broker)
    register_image_tools(mcp, broker)
    register_checkpoint_tools(mcp, broker)
    register_terminal_tools(mcp, broker)
    register_terminal_window_tools(mcp, broker)
    register_terminal_window_interaction_tools(mcp, broker)
    register_computer_tools(mcp, broker)


def registered_tool_names(mcp: FastMCP) -> tuple[str, ...]:
    # FastMCP 1.28.1 has no public synchronous inventory API.
    manager = mcp._tool_manager  # pyright: ignore[reportPrivateUsage]
    return tuple(tool.name for tool in manager.list_tools())
