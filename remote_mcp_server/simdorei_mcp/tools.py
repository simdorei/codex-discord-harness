from __future__ import annotations

from typing import Final
from uuid import uuid4

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from simdorei_mcp_common.messages import (
    BindProjectOutput,
    ListFilesOutput,
    ProjectInfoOutput,
    ReadFileCommand,
    ReadFileOutput,
    RequestId,
    WriteFileCommand,
    WriteFileOutput,
)

NO_AUTH_META: Final = {"securitySchemes": [{"type": "noauth"}]}
ToolContext = Context[ServerSession, None, object]
READ_ONLY_ANNOTATIONS: Final = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
WRITE_ANNOTATIONS: Final = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    openWorldHint=False,
)


class OpenAiToolIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    session: str = Field(alias="openai/session", min_length=1)
    subject: str = Field(alias="openai/subject", min_length=1)


def register_tools(mcp: FastMCP, broker: BindingBroker) -> None:
    @mcp.tool(
        title="Bind local project",
        annotations=WRITE_ANNOTATIONS,
        meta=NO_AUTH_META,
        structured_output=True,
    )
    async def bind_project(binding_code: str, ctx: ToolContext) -> BindProjectOutput:
        """Bind this ChatGPT conversation to the Codex thread that issued the one-time code."""
        identity = _identity(ctx)
        try:
            return await broker.bind(identity.session, identity.subject, binding_code)
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Show bound project",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=NO_AUTH_META,
        structured_output=True,
    )
    async def project_info(ctx: ToolContext) -> ProjectInfoOutput:
        """Show the project root and Codex thread bound to this conversation."""
        identity = _identity(ctx)
        try:
            return await broker.project_info(identity.session, identity.subject)
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="List project files",
        annotations=READ_ONLY_ANNOTATIONS,
        meta=NO_AUTH_META,
        structured_output=True,
    )
    async def list_project_files(
        ctx: ToolContext,
        pattern: str = "**/*",
        limit: int = 200,
    ) -> ListFilesOutput:
        """List non-sensitive files inside the bound project."""
        identity = _identity(ctx)
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
        meta=NO_AUTH_META,
        structured_output=True,
    )
    async def read_project_file(
        ctx: ToolContext,
        path: str,
        start_line: int = 1,
        max_lines: int = 250,
    ) -> ReadFileOutput:
        """Read a bounded UTF-8 section from a non-sensitive project file."""
        identity = _identity(ctx)
        try:
            return await broker.read_file(
                identity.session,
                identity.subject,
                ReadFileCommand(
                    request_id=RequestId(uuid4().hex),
                    thread_id="pending-route",
                    path=path,
                    start_line=start_line,
                    max_lines=max_lines,
                ),
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        title="Write project file",
        annotations=WRITE_ANNOTATIONS,
        meta=NO_AUTH_META,
        structured_output=True,
    )
    async def write_project_file(
        ctx: ToolContext,
        path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> WriteFileOutput:
        """Write UTF-8 text with optimistic conflict protection inside the bound project."""
        identity = _identity(ctx)
        try:
            return await broker.write_file(
                identity.session,
                identity.subject,
                WriteFileCommand(
                    request_id=RequestId(uuid4().hex),
                    thread_id="pending-route",
                    path=path,
                    content=content,
                    expected_sha256=expected_sha256,
                ),
            )
        except BrokerError as exc:
            raise ToolError(str(exc)) from exc


def _identity(ctx: ToolContext) -> OpenAiToolIdentity:
    meta = ctx.request_context.meta
    if meta is None:
        raise ToolError("ChatGPT did not provide conversation identity metadata.")
    try:
        return OpenAiToolIdentity.model_validate_json(
            meta.model_dump_json(by_alias=True)
        )
    except ValidationError as exc:
        raise ToolError(
            "ChatGPT did not provide both session and subject metadata."
        ) from exc
