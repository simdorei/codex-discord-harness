from __future__ import annotations

import hmac
from builtins import BaseExceptionGroup
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from contextlib import asynccontextmanager
from typing import assert_never

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, ValidationError

from remote_mcp_server.simdorei_mcp.bridge_sender import WebSocketBridgeSender
from remote_mcp_server.simdorei_mcp.broker import BindingBroker
from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    require_complete_tool_inventory,
)
from remote_mcp_server.simdorei_mcp.mcp_instructions import MCP_INSTRUCTIONS
from remote_mcp_server.simdorei_mcp.oauth_approval import create_approval_router
from remote_mcp_server.simdorei_mcp.oauth_provider import (
    SingleUserOAuthProvider,
    TokenCapacityError,
)
from remote_mcp_server.simdorei_mcp.oauth_scopes import (
    DEFAULT_OAUTH_SCOPES,
    OAUTH_SCOPES,
    READ_SCOPE,
)
from remote_mcp_server.simdorei_mcp.oauth_store import OAuthStore
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from remote_mcp_server.simdorei_mcp.tools import (
    register_tools,
    registered_tool_names,
)
from simdorei_mcp_common.messages import (
    BridgeHello,
    GatewayHello,
    ListFilesResult,
    OperationErrorResult,
    ProjectAck,
    ProjectInfoResult,
    ProjectOperationResult,
    ProjectSessionResult,
    ProjectUpsert,
    ReadFileResult,
    WriteFileResult,
    parse_bridge_message,
)

LOGGER = structlog.get_logger()


class GatewayConfigurationError(Exception):
    """Raised when a required public gateway setting is incomplete."""


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    service: str
    upstream_ready: bool


def create_app(settings: GatewaySettings) -> FastAPI:
    broker = BindingBroker(
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    public_base_url = str(settings.public_base_url).rstrip("/")
    resource_url = f"{public_base_url}/mcp"
    oauth_provider = SingleUserOAuthProvider(
        store=OAuthStore(
            settings.oauth_database_path,
            max_clients=settings.oauth_client_limit,
            max_token_families=settings.oauth_token_family_global_limit,
            max_token_families_per_client=(
                settings.oauth_token_family_per_client_limit
            ),
            max_refresh_history_global=(
                settings.oauth_refresh_history_global_limit
            ),
            max_refresh_history_per_family=(
                settings.oauth_refresh_history_per_family_limit
            ),
        ),
        owner_token=settings.owner_token,
        public_base_url=public_base_url,
        resource_url=resource_url,
        access_token_seconds=settings.oauth_access_token_seconds,
        refresh_token_seconds=settings.oauth_refresh_token_seconds,
        pending_authorization_limit=(settings.oauth_pending_authorization_limit),
        authorization_code_limit=settings.oauth_authorization_code_global_limit,
        authorization_code_per_client_limit=(
            settings.oauth_authorization_code_per_client_limit
        ),
    )
    mcp = FastMCP(
        "simdorei-local-project",
        instructions=MCP_INSTRUCTIONS,
        auth_server_provider=oauth_provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(str(settings.public_base_url)),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=[READ_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=OAUTH_SCOPES,
                default_scopes=DEFAULT_OAUTH_SCOPES,
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                _public_host(settings),
                "localhost",
                "localhost:*",
                "127.0.0.1",
                "127.0.0.1:*",
            ],
            allowed_origins=[str(settings.public_base_url).rstrip("/")],
        ),
    )
    register_tools(mcp, broker)
    _ = require_complete_tool_inventory(registered_tool_names(mcp))
    mcp_app = mcp.streamable_http_app()
    mcp_app.add_exception_handler(TokenCapacityError, _token_capacity_error_response)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        async with _close_preserving_lifespan(
            mcp.session_manager.run(),
            oauth_provider.close,
        ):
            yield

    app = FastAPI(
        title="Simdorei Local Project MCP",
        lifespan=lifespan,
    )
    public_path = (settings.public_base_url.path or "").rstrip("/")
    approval_path = f"{public_path}/oauth/approve"
    app.include_router(
        create_approval_router(oauth_provider, approval_path=approval_path)
    )

    @app.get("/healthz")
    async def health() -> HealthResponse:
        bridge_connected = await broker.is_device_connected(settings.device_id)
        return HealthResponse(
            ok=True,
            service="simdorei-local-project-mcp",
            upstream_ready=bridge_connected,
        )

    @app.websocket("/bridge")
    async def bridge(socket: WebSocket) -> None:
        expected_device_id = settings.device_id
        if not _authorized(socket, settings):
            await socket.close(code=1008, reason="unauthorized")
            return
        await socket.accept()
        sender = WebSocketBridgeSender(socket)
        attached = False
        try:
            first = parse_bridge_message(await socket.receive_text())
            match first:
                case BridgeHello(device_id=device_id) if (
                    device_id == expected_device_id
                ):
                    displaced = await broker.attach(device_id, sender)
                    attached = True
                    if displaced is not None:
                        await displaced.close()
                    await sender.send_control(GatewayHello())
                    LOGGER.info("bridge.connected", device_id=device_id)
                case BridgeHello():
                    await sender.reject(1008, "device mismatch")
                    return
                case (
                    ProjectUpsert()
                    | ProjectInfoResult()
                    | ListFilesResult()
                    | ReadFileResult()
                    | WriteFileResult()
                    | ProjectOperationResult()
                    | ProjectSessionResult()
                    | OperationErrorResult()
                ):
                    await sender.reject(1002, "hello required")
                    return
                # Preserve a runtime guard at the validated WebSocket boundary.
                case unreachable:  # pyright: ignore[reportUnnecessaryComparison]
                    assert_never(unreachable)
            while True:
                message = parse_bridge_message(await socket.receive_text())
                match message:
                    case ProjectUpsert(
                        project_scope=project_scope,
                        binding_id=binding_id,
                    ):
                        await broker.upsert(expected_device_id, sender, message)
                        await sender.send_control(
                            ProjectAck(
                                project_scope=project_scope,
                                binding_id=binding_id,
                            )
                        )
                        continue
                    case (
                        ProjectInfoResult()
                        | ListFilesResult()
                        | ReadFileResult()
                        | WriteFileResult()
                        | ProjectOperationResult()
                        | ProjectSessionResult()
                        | OperationErrorResult()
                    ):
                        await broker.complete(expected_device_id, sender, message)
                        continue
                    case BridgeHello():
                        await sender.reject(1002, "duplicate hello")
                        return
                assert_never(message)
        except WebSocketDisconnect:
            LOGGER.info("bridge.disconnected", device_id=expected_device_id)
        except (ValidationError, BrokerError) as exc:
            LOGGER.warning(
                "bridge.protocol_rejected",
                device_id=expected_device_id,
                error_type=type(exc).__name__,
            )
            await sender.reject(1008, "invalid bridge message")
        finally:
            if attached:
                await broker.detach(expected_device_id, sender)

    app.mount("/", mcp_app)
    return app


@asynccontextmanager
async def _close_preserving_lifespan(
    session_context: AbstractAsyncContextManager[None],
    close_oauth: Callable[[], Awaitable[None]],
) -> AsyncGenerator[None, None]:
    try:
        async with session_context:
            yield
    except BaseException as session_error:  # noqa: BLE001 - preserve teardown failures.
        try:
            await close_oauth()
        except BaseException as close_error:  # noqa: BLE001 - preserve both failures.
            raise BaseExceptionGroup(
                "MCP session and OAuth shutdown failures",
                (session_error, close_error),
            ) from None
        raise
    await close_oauth()


def _authorized(socket: WebSocket, settings: GatewaySettings) -> bool:
    authorization = socket.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    candidate = authorization.removeprefix(prefix)
    return hmac.compare_digest(
        candidate,
        settings.device_token.get_secret_value(),
    )


def _public_host(settings: GatewaySettings) -> str:
    host = settings.public_base_url.host
    if host is None:
        raise GatewayConfigurationError(
            "SIMDOREI_MCP_PUBLIC_BASE_URL must include a host."
        )
    return host


async def _token_capacity_error_response(
    _request: Request,
    _error: Exception,
) -> JSONResponse:
    return JSONResponse(
        {
            "error": "temporarily_unavailable",
            "error_description": "OAuth token storage capacity is full.",
        },
        status_code=503,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
