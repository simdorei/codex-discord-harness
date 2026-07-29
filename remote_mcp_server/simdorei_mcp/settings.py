from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from simdorei_mcp_common.messages import DeviceId


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SIMDOREI_MCP_",
        frozen=True,
        extra="ignore",
    )

    device_id: DeviceId = Field(min_length=1, max_length=200)
    device_token: SecretStr
    public_base_url: HttpUrl
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def load_gateway_settings() -> GatewaySettings:
    values: dict[str, str] = {
        "device_id": _required_environment("SIMDOREI_MCP_DEVICE_ID"),
        "device_token": _required_environment("SIMDOREI_MCP_DEVICE_TOKEN"),
        "public_base_url": _required_environment("SIMDOREI_MCP_PUBLIC_BASE_URL"),
    }
    optional = {
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
        raise RuntimeError(f"{name} is required.")
    return value
