from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import assert_never

import anyio
import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, ValidationError

from remote_mcp_server.simdorei_mcp.broker import BindingBroker, BridgeSender
from remote_mcp_server.simdorei_mcp.broker_errors import BrokerError
from remote_mcp_server.simdorei_mcp.settings import GatewaySettings
from remote_mcp_server.simdorei_mcp.tools import register_tools
from simdorei_mcp_common.messages import (
    BindingAck,
    BindingUpsert,
    BridgeHello,
    GatewayCommand,
    GatewayHello,
    ListFilesResult,
    OperationErrorResult,
    ProjectInfoResult,
    ReadFileResult,
    WriteFileResult,
    parse_bridge_message,
)

LOGGER = structlog.get_logger()


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    service: str
    upstream_ready: bool


class WebSocketBridgeSender(BridgeSender):
    """Serializes concurrent MCP tool requests onto one device socket."""

    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket
        self._send_lock = anyio.Lock()

    async def send(self, command: GatewayCommand) -> None:
        async with self._send_lock:
            await self._socket.send_text(command.model_dump_json())


def create_app(settings: GatewaySettings) -> FastAPI:
    broker = BindingBroker(
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    mcp = FastMCP(
        "simdorei-local-project",
        instructions=(
            "Bind with the one-time code supplied by Codex, then inspect or edit only "
            "the bound local project. Read before writing and pass the returned SHA-256 "
            "when updating an existing file."
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
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="Simdorei Local Project MCP",
        lifespan=lifespan,
    )

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
                case BridgeHello(device_id=device_id) if device_id == expected_device_id:
                    await broker.attach(device_id, sender)
                    attached = True
                    await socket.send_text(GatewayHello().model_dump_json())
                    LOGGER.info("bridge.connected", device_id=device_id)
                case BridgeHello():
                    await socket.close(code=1008, reason="device mismatch")
                    return
                case (
                    BindingUpsert()
                    | ProjectInfoResult()
                    | ListFilesResult()
                    | ReadFileResult()
                    | WriteFileResult()
                    | OperationErrorResult()
                ):
                    await socket.close(code=1002, reason="hello required")
                    return
                case unreachable:
                    assert_never(unreachable)
            while True:
                message = parse_bridge_message(await socket.receive_text())
                match message:
                    case BindingUpsert(binding_code=code):
                        await broker.upsert(expected_device_id, message)
                        await socket.send_text(
                            BindingAck(binding_code=code).model_dump_json()
                        )
                    case (
                        ProjectInfoResult()
                        | ListFilesResult()
                        | ReadFileResult()
                        | WriteFileResult()
                        | OperationErrorResult()
                    ):
                        await broker.complete(expected_device_id, message)
                    case BridgeHello():
                        await socket.close(code=1002, reason="duplicate hello")
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
            await socket.close(code=1008, reason="invalid bridge message")
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
        raise ValueError("SIMDOREI_MCP_PUBLIC_BASE_URL must include a host.")
    return host
