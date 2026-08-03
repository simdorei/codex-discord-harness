from __future__ import annotations

import pytest
from pydantic import ValidationError

from simdorei_mcp_common.messages import (
    BridgeHello,
    DeviceId,
    RequestId,
    RuntimeCapabilityCommand,
    RuntimeCapabilityResult,
    parse_bridge_message,
)
from simdorei_mcp_common.runtime_provenance import RuntimeProvenanceEnvelope


def test_protocol_eleven_rejects_an_old_protocol_ten_bridge() -> None:
    current = BridgeHello(
        protocol_version=11,
        device_id=DeviceId("device-a"),
    ).model_dump_json()
    assert isinstance(parse_bridge_message(current), BridgeHello)

    with pytest.raises(ValidationError):
        _ = parse_bridge_message(
            '{"type":"hello","protocol_version":10,"device_id":"device-a"}'
        )


def test_runtime_capability_messages_require_gateway_and_cycle_bindings() -> None:
    with pytest.raises(ValidationError, match="runtime provenance is required"):
        _ = RuntimeCapabilityCommand(
            request_id=RequestId("capability-missing-provenance"),
            thread_id="thread-a",
            computer_session_id="computer-session-a",
            inventory_sha256="a" * 64,
        )

    command = RuntimeCapabilityCommand(
        request_id=RequestId("capability-valid"),
        thread_id="thread-a",
        computer_session_id="computer-session-a",
        runtime_provenance=RuntimeProvenanceEnvelope(
            session_binding_sha256="b" * 64,
        ),
        inventory_sha256="a" * 64,
    )
    assert command.tool_count == 47

    with pytest.raises(ValidationError, match="require a cycle binding"):
        _ = RuntimeCapabilityResult(
            request_id=command.request_id,
            status="accepted",
        )
