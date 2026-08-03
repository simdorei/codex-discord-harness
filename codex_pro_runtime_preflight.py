from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast, override

import codex_app_server_transport as app_server_transport
from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_plugin_runtime_fingerprint import (
    BROWSER_PLUGIN_ID,
    REMOTE_PLUGIN_ID,
    PluginRuntimeFingerprintError,
    fingerprint_required_plugins,
    read_codex_plugin_inventory,
)

PLUGIN_MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "plugins/codex-discord-remote/.codex-plugin/plugin.json"
)

InventoryReader = Callable[[], str]
ResidentSnapshotReader = Callable[[], AppServerLifecycleSnapshot]


@dataclass(frozen=True, slots=True)
class ProRuntimePreflightError(Exception):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ProRuntimeStatus:
    remote_plugin_version: str
    browser_plugin_version: str
    resident_generation: int


def expected_remote_plugin_version(path: Path = PLUGIN_MANIFEST_PATH) -> str:
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProRuntimePreflightError(
            f"remote plugin manifest is unavailable: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ProRuntimePreflightError("remote plugin manifest must be a JSON object")
    version = cast("dict[str, object]", raw).get("version")
    if not isinstance(version, str) or not version:
        raise ProRuntimePreflightError(
            "remote plugin manifest.version must be a non-empty string"
        )
    return version


def verify_pro_runtime(
    *,
    inventory_json: str,
    expected_remote_version: str,
    resident_snapshot: AppServerLifecycleSnapshot,
    current_plugin_fingerprint: str,
) -> ProRuntimeStatus:
    inventory = _inventory_object(inventory_json)
    plugins = _plugin_records(inventory.get("installed"))
    remote = _required_plugin(plugins, REMOTE_PLUGIN_ID)
    browser = _required_plugin(plugins, BROWSER_PLUGIN_ID)
    remote_version = _enabled_version(remote, REMOTE_PLUGIN_ID)
    browser_version = _enabled_version(browser, BROWSER_PLUGIN_ID)
    if remote_version != expected_remote_version:
        raise ProRuntimePreflightError(
            f"plugin {REMOTE_PLUGIN_ID!r} version mismatch: expected "
            + f"{expected_remote_version!r}, got {remote_version!r}"
        )
    if not resident_snapshot.healthy or resident_snapshot.accepting_since is None:
        raise ProRuntimePreflightError(
            "resident Codex app-server is not healthy "
            + f"(generation {resident_snapshot.generation})"
        )
    if resident_snapshot.plugin_runtime_error is not None:
        raise ProRuntimePreflightError(
            "resident Codex app-server plugin snapshot failed: "
            + resident_snapshot.plugin_runtime_error
        )
    resident_fingerprint = resident_snapshot.plugin_runtime_fingerprint
    if resident_fingerprint is None:
        raise ProRuntimePreflightError(
            "resident Codex app-server has no plugin snapshot "
            + f"(generation {resident_snapshot.generation})"
        )
    if resident_fingerprint != current_plugin_fingerprint:
        raise ProRuntimePreflightError(
            "resident Codex app-server plugin snapshot is stale "
            + f"(generation {resident_snapshot.generation}); restart the remote bot"
        )
    return ProRuntimeStatus(
        remote_plugin_version=remote_version,
        browser_plugin_version=browser_version,
        resident_generation=resident_snapshot.generation,
    )


def run_pro_runtime_preflight(
    *,
    inventory_reader: InventoryReader = read_codex_plugin_inventory,
    resident_snapshot_reader: ResidentSnapshotReader = (
        app_server_transport.DEFAULT_CLIENT.lifecycle_snapshot
    ),
    manifest_path: Path = PLUGIN_MANIFEST_PATH,
) -> ProRuntimeStatus:
    inventory_json = inventory_reader()
    try:
        current_fingerprint = fingerprint_required_plugins(inventory_json)
    except PluginRuntimeFingerprintError as exc:
        raise ProRuntimePreflightError(
            f"current Codex plugin fingerprint failed: {exc}"
        ) from exc
    return verify_pro_runtime(
        inventory_json=inventory_json,
        expected_remote_version=expected_remote_plugin_version(manifest_path),
        resident_snapshot=resident_snapshot_reader(),
        current_plugin_fingerprint=current_fingerprint,
    )


def _inventory_object(inventory_json: str) -> dict[str, object]:
    try:
        raw = cast(object, json.loads(inventory_json))
    except json.JSONDecodeError as exc:
        raise ProRuntimePreflightError(
            f"Codex plugin inventory is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise ProRuntimePreflightError("Codex plugin inventory must be a JSON object")
    return cast("dict[str, object]", raw)


def _plugin_records(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        raise ProRuntimePreflightError(
            "Codex plugin inventory.installed must be a JSON array"
        )
    records: list[dict[str, object]] = []
    for item in cast("list[object]", value):
        if not isinstance(item, dict):
            raise ProRuntimePreflightError(
                "Codex plugin inventory entries must be JSON objects"
            )
        records.append(cast("dict[str, object]", item))
    return tuple(records)


def _required_plugin(
    records: tuple[dict[str, object], ...],
    plugin_id: str,
) -> dict[str, object]:
    matches = tuple(record for record in records if record.get("pluginId") == plugin_id)
    if len(matches) != 1:
        raise ProRuntimePreflightError(
            f"plugin {plugin_id!r} was not installed exactly once"
        )
    return matches[0]


def _enabled_version(record: dict[str, object], plugin_id: str) -> str:
    if record.get("installed") is not True:
        raise ProRuntimePreflightError(f"plugin {plugin_id!r} is not installed")
    if record.get("enabled") is not True:
        raise ProRuntimePreflightError(f"plugin {plugin_id!r} is not enabled")
    version = record.get("version")
    if not isinstance(version, str) or not version:
        raise ProRuntimePreflightError(
            f"plugin {plugin_id!r} version must be a non-empty string"
        )
    return version
