from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast


PROTOCOL: Final = "ask-chatgpt-pro-connector-control-v1"
CONNECTOR_NAME: Final = "Simdorei Local Project Oauth"
CONNECTOR_PATH: Final = "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"
EXPECTED_PROBE_SHA256: Final = (
    "e5de2ef92ac6fca49442e60f237888bea47b5d6090c3347c180d103114ced8dc"
)
PLUGIN_DATA_DIRECTORY: Final = "codex-discord-remote-codex-discord-remote"


class ProConnectorUnavailableError(RuntimeError):
    def __init__(self, internal_detail: str) -> None:
        super().__init__("pro_connector_unavailable")
        self.internal_detail = internal_detail


def default_plugin_data_path() -> Path:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured_home) if configured_home else Path.home() / ".codex"
    return codex_home / "plugins" / "data" / PLUGIN_DATA_DIRECTORY


def require_verified_evidence(
    session_id: str,
    turn_id: str,
    *,
    plugin_data: Path | None = None,
) -> None:
    evidence = _read_receipt(
        session_id,
        turn_id,
        plugin_data or default_plugin_data_path(),
    )
    if evidence is None:
        raise ProConnectorUnavailableError(
            "Pro turn completed without exact-turn connector control evidence."
        )
    if evidence.get("status") != "verified":
        stage = evidence.get("failed_stage")
        raise ProConnectorUnavailableError(
            f"Chrome connector control was not verified: stage={stage or 'unknown'}"
        )
    expected = {
        "connector_name": CONNECTOR_NAME,
        "connector_path": CONNECTOR_PATH,
        "chat_mode": "chat",
        "pro_mode": True,
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ProConnectorUnavailableError(
            "Connector, Chat mode, or Pro mode evidence did not match the contract."
        )


def _read_receipt(
    session_id: str,
    turn_id: str,
    plugin_data: Path,
) -> dict[str, object] | None:
    if not session_id or not turn_id:
        return None
    key = hashlib.sha256(f"{session_id}\0{turn_id}".encode()).hexdigest()
    path = plugin_data / "pro-connector-evidence" / f"{key}.json"
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
    evidence = _object_map(raw)
    if evidence is None:
        return None
    if (
        evidence.get("protocol") != PROTOCOL
        or evidence.get("session_id") != session_id
        or evidence.get("turn_id") != turn_id
        or evidence.get("browser_type") != "chrome"
        or evidence.get("probe_sha256") != EXPECTED_PROBE_SHA256
    ):
        return None
    return evidence


def _object_map(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    values = cast(Mapping[object, object], raw)
    if not all(isinstance(key, str) for key in values):
        return None
    return {cast(str, key): value for key, value in values.items()}
