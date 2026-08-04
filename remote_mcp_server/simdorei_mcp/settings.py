from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from simdorei_mcp_common.messages import DeviceId
from simdorei_mcp_common.request_deadlines import (
    GATEWAY_REQUEST_TIMEOUT_SECONDS,
)


class GatewaySettingsError(RuntimeError):
    """Raised when a required gateway setting is unavailable."""


class GatewaySettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="SIMDOREI_MCP_",
        frozen=True,
        extra="ignore",
    )

    device_id: DeviceId = Field(min_length=1, max_length=200)
    device_token: SecretStr
    public_base_url: HttpUrl
    owner_token: SecretStr = Field(min_length=24)
    oauth_database_path: Path = Path("/data/oauth.sqlite3")
    oauth_access_token_seconds: int = Field(default=3600, ge=300, le=86400)
    oauth_refresh_token_seconds: int = Field(
        default=60 * 60 * 24 * 30,
        ge=3600,
        le=60 * 60 * 24 * 365,
    )
    oauth_pending_authorization_limit: int = Field(
        default=100,
        ge=1,
        le=1_000,
    )
    oauth_authorization_code_global_limit: int = Field(
        default=1_024,
        ge=1,
        le=100_000,
    )
    oauth_authorization_code_per_client_limit: int = Field(
        default=64,
        ge=1,
        le=10_000,
    )
    oauth_client_limit: int = Field(default=500, ge=10, le=10_000)
    oauth_token_family_global_limit: int = Field(
        default=256,
        ge=1,
        le=100_000,
    )
    oauth_token_family_per_client_limit: int = Field(
        default=16,
        ge=1,
        le=10_000,
    )
    oauth_refresh_history_global_limit: int = Field(
        default=65_536,
        ge=1,
        le=1_000_000,
    )
    oauth_refresh_history_per_family_limit: int = Field(
        default=1_024,
        ge=1,
        le=100_000,
    )
    request_timeout_seconds: float = Field(
        default=GATEWAY_REQUEST_TIMEOUT_SECONDS,
        ge=GATEWAY_REQUEST_TIMEOUT_SECONDS,
        le=7_200,
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def load_gateway_settings() -> GatewaySettings:
    values: dict[str, str] = {
        "device_id": _required_environment("SIMDOREI_MCP_DEVICE_ID"),
        "device_token": _required_environment("SIMDOREI_MCP_DEVICE_TOKEN"),
        "public_base_url": _required_environment("SIMDOREI_MCP_PUBLIC_BASE_URL"),
        "owner_token": _required_environment("SIMDOREI_MCP_OWNER_TOKEN"),
    }
    optional = {
        "oauth_database_path": "SIMDOREI_MCP_OAUTH_DATABASE_PATH",
        "oauth_access_token_seconds": "SIMDOREI_MCP_OAUTH_ACCESS_TOKEN_SECONDS",
        "oauth_refresh_token_seconds": "SIMDOREI_MCP_OAUTH_REFRESH_TOKEN_SECONDS",
        "oauth_pending_authorization_limit": (
            "SIMDOREI_MCP_OAUTH_PENDING_AUTHORIZATION_LIMIT"
        ),
        "oauth_client_limit": "SIMDOREI_MCP_OAUTH_CLIENT_LIMIT",
        "oauth_authorization_code_global_limit": (
            "SIMDOREI_MCP_OAUTH_AUTHORIZATION_CODE_GLOBAL_LIMIT"
        ),
        "oauth_authorization_code_per_client_limit": (
            "SIMDOREI_MCP_OAUTH_AUTHORIZATION_CODE_PER_CLIENT_LIMIT"
        ),
        "oauth_token_family_global_limit": (
            "SIMDOREI_MCP_OAUTH_TOKEN_FAMILY_GLOBAL_LIMIT"
        ),
        "oauth_token_family_per_client_limit": (
            "SIMDOREI_MCP_OAUTH_TOKEN_FAMILY_PER_CLIENT_LIMIT"
        ),
        "oauth_refresh_history_global_limit": (
            "SIMDOREI_MCP_OAUTH_REFRESH_HISTORY_GLOBAL_LIMIT"
        ),
        "oauth_refresh_history_per_family_limit": (
            "SIMDOREI_MCP_OAUTH_REFRESH_HISTORY_PER_FAMILY_LIMIT"
        ),
        "request_timeout_seconds": "SIMDOREI_MCP_REQUEST_TIMEOUT_SECONDS",
        "log_level": "SIMDOREI_MCP_LOG_LEVEL",
    }
    for field_name, environment_name in optional.items():
        value = os.environ.get(environment_name, "").strip()
        if value:
            values[field_name] = value
    return GatewaySettings.model_validate(values)


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GatewaySettingsError(f"{name} is required.")
    return value
