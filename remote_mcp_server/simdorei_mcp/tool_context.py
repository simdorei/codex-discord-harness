from __future__ import annotations

from dataclasses import dataclass
from typing import Final, TypeVar

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from remote_mcp_server.simdorei_mcp.oauth_provider import READ_SCOPE, WRITE_SCOPE
from simdorei_mcp_common.operation_outputs import OperationOutput
from simdorei_mcp_common.operation_requests import ProjectOperation

READ_AUTH_META: Final = {
    "securitySchemes": [{"type": "oauth2", "scopes": [READ_SCOPE]}]
}
WRITE_AUTH_META: Final = {
    "securitySchemes": [
        {"type": "oauth2", "scopes": [READ_SCOPE, WRITE_SCOPE]}
    ]
}
ToolContext = Context[ServerSession, None, object]
OutputT = TypeVar("OutputT", bound=OperationOutput)
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
OPEN_WORLD_ANNOTATIONS: Final = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    openWorldHint=True,
)
SELECT_ANNOTATIONS: Final = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    openWorldHint=False,
)


class OpenAiToolSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    session: str = Field(alias="openai/session", min_length=1)


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    session: str
    subject: str


def tool_identity(ctx: ToolContext, required_scope: str) -> ToolIdentity:
    meta = ctx.request_context.meta
    if meta is None:
        raise ToolError("ChatGPT did not provide conversation identity metadata.")
    try:
        session = OpenAiToolSession.model_validate_json(
            meta.model_dump_json(by_alias=True)
        )
    except ValidationError as exc:
        raise ToolError(
            "ChatGPT did not provide conversation session metadata."
        ) from exc
    access_token = get_access_token()
    if access_token is None or access_token.subject is None:
        raise ToolError("ChatGPT OAuth identity is unavailable.")
    if required_scope not in access_token.scopes:
        raise ToolError(f"OAuth scope {required_scope} is required.")
    return ToolIdentity(session=session.session, subject=access_token.subject)


async def execute_operation(
    ctx: ToolContext,
    broker: BindingBroker,
    operation: ProjectOperation,
    output_type: type[OutputT],
    *,
    required_scope: str,
) -> OutputT:
    identity = tool_identity(ctx, required_scope)
    try:
        output = await broker.project_operation(
            identity.session,
            identity.subject,
            operation,
        )
    except BrokerError as exc:
        raise ToolError(str(exc)) from exc
    if not isinstance(output, output_type):
        raise ToolError(
            "The local bridge returned an unexpected operation result."
        )
    return output
