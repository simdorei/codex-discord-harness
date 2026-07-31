from __future__ import annotations

import hashlib

from simdorei_mcp_common.messages import GatewayCommand


def command_fingerprint(command: GatewayCommand) -> str:
    payload = command.model_dump_json(exclude={"deadline_at"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["command_fingerprint"]
