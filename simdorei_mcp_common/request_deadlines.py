from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from simdorei_mcp_common.operation_requests import (
    CommandRunRequest,
    ProjectOperation,
)
from simdorei_mcp_common.terminal_protocol import TerminalExecRequest


DEFAULT_REQUEST_LIFETIME_SECONDS: Final = 60
TRANSPORT_GRACE_SECONDS: Final = 15
MAX_COMMAND_RUN_SECONDS: Final = 300
MAX_TERMINAL_EXEC_SECONDS: Final = 3_600
COMMAND_REQUEST_LIFETIME_SECONDS: Final = (
    MAX_COMMAND_RUN_SECONDS + TRANSPORT_GRACE_SECONDS
)
MAX_REQUEST_LIFETIME_SECONDS: Final = (
    MAX_TERMINAL_EXEC_SECONDS + TRANSPORT_GRACE_SECONDS
)
GATEWAY_REQUEST_TIMEOUT_SECONDS: Final = MAX_REQUEST_LIFETIME_SECONDS + 15


def default_request_deadline() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=DEFAULT_REQUEST_LIFETIME_SECONDS)


def operation_request_deadline(operation: ProjectOperation) -> datetime:
    lifetime = DEFAULT_REQUEST_LIFETIME_SECONDS
    if isinstance(operation, CommandRunRequest):
        lifetime = operation.timeout_seconds + TRANSPORT_GRACE_SECONDS
    if isinstance(operation, TerminalExecRequest):
        lifetime = operation.timeout_seconds + TRANSPORT_GRACE_SECONDS
    return datetime.now(UTC) + timedelta(seconds=lifetime)


__all__ = [
    "DEFAULT_REQUEST_LIFETIME_SECONDS",
    "COMMAND_REQUEST_LIFETIME_SECONDS",
    "GATEWAY_REQUEST_TIMEOUT_SECONDS",
    "MAX_REQUEST_LIFETIME_SECONDS",
    "MAX_TERMINAL_EXEC_SECONDS",
    "default_request_deadline",
    "operation_request_deadline",
]
