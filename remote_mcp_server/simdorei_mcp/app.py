from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import assert_never

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
from remote_mcp_server.simdorei_mcp.oauth_approval import create_approval_router
from remote_mcp_server.simdorei_mcp.oauth_provider import SingleUserOAuthProvider
from remote_mcp_server.simdorei_mcp.oauth_scopes import (
    DEFAULT_OAUTH_SCOPES,
    OAUTH_SCOPES,
    READ_SCOPE,
)
from remote_mcp_server.simdorei_mcp.oauth_store import OAuthStore
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from remote_mcp_server.simdorei_mcp.tools import register_tools
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
        store=OAuthStore(settings.oauth_database_path),
        owner_token=settings.owner_token,
        public_base_url=public_base_url,
        resource_url=resource_url,
        access_token_seconds=settings.oauth_access_token_seconds,
        refresh_token_seconds=settings.oauth_refresh_token_seconds,
    )
    mcp = FastMCP(
        "simdorei-local-project",
        instructions=(
            "Call select_project once with the project scope supplied by Codex. "
            "Then inspect or edit only that local project. Read existing files "
            "before changing them and pass their SHA-256 values to file_apply_patch. "
            "Use retrieve_image when visual inspection is needed. Run only commands "
            "returned by command_list. Review repo_status and show_changes before "
            "git_commit or git_push. For computer use, first launch an isolated "
            "Chrome or blank Notepad window, list and activate that session-owned "
            "window. Only Notepad can be captured. Spend each Notepad "
            "observation ID on exactly one action within 30 seconds. Take a new "
            "screenshot after every action. Chrome allows launch, listing, "
            "activation, and emergency stop only because web pixels can contain "
            "unverifiable secret surfaces. Clipboard "
            "writes also require a fresh Notepad observation. Never operate "
            "ChatGPT, Codex, terminals, "
            "password managers, remote desktop, security/privacy, sign-in, password, "
            "OTP, UAC, or CAPTCHA surfaces; leave those steps to the user."
        ),
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
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await oauth_provider.close()

    app = FastAPI(
        title="Simdorei Local Project MCP",
        lifespan=lifespan,
    )
    app.include_router(create_approval_router(oauth_provider))

    @app.get("/healthz")
    async def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            service="simdorei-local-project-mcp",
            upstream_ready=True,
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
                case unreachable:
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
                    case BridgeHello():
                        await sender.reject(1002, "duplicate hello")
                        return
                    case unreachable:
                        assert_never(unreachable)
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
