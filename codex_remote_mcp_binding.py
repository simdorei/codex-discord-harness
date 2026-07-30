from __future__ import annotations

import threading
from pathlib import Path

from codex_remote_mcp_bridge import LogFunc, RemoteMcpBridge
from codex_remote_mcp_bridge_config import (
    ProjectTicket,
    RemoteMcpBridgeConfig,
    load_remote_mcp_config,
)

_BRIDGE_LOCK = threading.Lock()
_bridge: RemoteMcpBridge | None = None
_bridge_config: RemoteMcpBridgeConfig | None = None


def register_remote_mcp_project(
    thread_id: str,
    project_scope: str,
    root: Path,
    log: LogFunc,
) -> ProjectTicket | None:
    config = load_remote_mcp_config()
    if config is None:
        return None
    bridge = _get_bridge(config, log)
    return bridge.register_project(thread_id, project_scope, root)


def close_remote_mcp_bridge() -> None:
    global _bridge, _bridge_config
    with _BRIDGE_LOCK:
        bridge = _bridge
        _bridge = None
        _bridge_config = None
    if bridge is not None:
        bridge.close()


def _get_bridge(config: RemoteMcpBridgeConfig, log: LogFunc) -> RemoteMcpBridge:
    global _bridge, _bridge_config
    with _BRIDGE_LOCK:
        if _bridge is not None and _bridge_config == config:
            return _bridge
        previous = _bridge
        _bridge = RemoteMcpBridge(config, log=log)
        _bridge_config = config
    if previous is not None:
        previous.close()
    return _bridge
