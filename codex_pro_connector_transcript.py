from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias, assert_never, cast


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
RecordKind: TypeAlias = Literal["call", "output"]

PROTOCOL: Final = "ask-chatgpt-pro-connector-control-v1"
CONNECTOR_NAME: Final = "Simdorei Local Project Oauth"
CONNECTOR_PATH: Final = "/plugins/plugin_asdk_app_6a6ae90be0a08191b877eddba93b631c"
EXPECTED_PROBE_SHA256: Final = (
    "e5de2ef92ac6fca49442e60f237888bea47b5d6090c3347c180d103114ced8dc"
)
PLUGIN_RELATIVE_PATH: Final = Path("plugins/codex-discord-remote")
PROBE_RELATIVE_PATH: Final = Path(
    "skills/ask-chatgpt-pro/scripts/pro_connector_control.mjs"
)


@dataclass(frozen=True, slots=True)
class TranscriptEvidenceSource:
    codex_home: Path
    plugin_root: Path


def default_evidence_source() -> TranscriptEvidenceSource:
    configured_home = os.environ.get("CODEX_HOME", "").strip()
    codex_home = Path(configured_home) if configured_home else Path.home() / ".codex"
    plugin_root = Path(__file__).resolve().parent / PLUGIN_RELATIVE_PATH
    return TranscriptEvidenceSource(codex_home=codex_home, plugin_root=plugin_root)


def canonical_inner_probe_code(plugin_root: Path) -> str:
    probe_uri = (plugin_root / PROBE_RELATIVE_PATH).resolve().as_uri()
    encoded_uri = json.dumps(probe_uri, ensure_ascii=True)
    return (
        "nodeRepl.write(JSON.stringify(await (await import("
        f"{encoded_uri})).prepareProConnector(globalThis)));"
    )


def canonical_probe_code(plugin_root: Path) -> str:
    tool_input = json.dumps(
        {
            "code": canonical_inner_probe_code(plugin_root),
            "title": "Prepare Pro OAuth connector",
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        "const connectorControlResult = await "
        f"tools.mcp__node_repl__js({tool_input});\n"
        "for (const item of connectorControlResult.content ?? []) {\n"
        '  if (item.type === "text") text(item.text);\n'
        "}"
    )


def read_transcript_evidence(
    session_id: str,
    turn_id: str,
    source: TranscriptEvidenceSource,
) -> JsonObject | None:
    if not session_id or not turn_id or not _probe_integrity_ok(source.plugin_root):
        return None
    outer_code = canonical_probe_code(source.plugin_root)
    inner_code = canonical_inner_probe_code(source.plugin_root)
    latest_call_id: str | None = None
    latest_evidence: JsonObject | None = None
    output_count = 0
    for transcript in _transcript_paths(source.codex_home, session_id):
        try:
            lines = transcript.open(encoding="utf-8")
        except OSError:
            continue
        with lines:
            for line in lines:
                payload = _response_payload(line)
                if payload is None or _payload_turn_id(payload) != turn_id:
                    continue
                call_id = payload.get("call_id")
                if not isinstance(call_id, str) or not call_id:
                    continue
                kind = _record_kind(payload)
                match kind:
                    case "call":
                        if _trusted_call(payload, outer_code, inner_code):
                            latest_call_id = call_id
                            latest_evidence = None
                            output_count = 0
                    case "output":
                        if call_id != latest_call_id:
                            continue
                        output_count += 1
                        latest_evidence = (
                            _single_valid_evidence(payload.get("output"))
                            if output_count == 1
                            else None
                        )
                    case None:
                        continue
                    case unreachable:
                        assert_never(unreachable)
    return latest_evidence if output_count == 1 else None


def _trusted_call(payload: Mapping[str, JsonValue], outer: str, inner: str) -> bool:
    name = payload.get("name")
    code = payload.get("input")
    if not isinstance(name, str) or not isinstance(code, str):
        return False
    expected = {
        "exec": outer,
        "functions.exec": outer,
        "js": inner,
        "mcp__node_repl__js": inner,
        "node_repl.js": inner,
    }.get(name)
    if expected is None:
        return False
    return code in {expected, f"{expected}\n", f"{expected}\r\n"}


def _record_kind(payload: Mapping[str, JsonValue]) -> RecordKind | None:
    value = payload.get("type")
    if not isinstance(value, str):
        return None
    return {
        "custom_tool_call": "call",
        "custom_tool_call_output": "output",
    }.get(value)


def _transcript_paths(codex_home: Path, session_id: str) -> tuple[Path, ...]:
    pattern = f"**/rollout-*-{session_id}.jsonl"
    paths: list[Path] = []
    for root_name in ("sessions", "archived_sessions"):
        root = codex_home / root_name
        if root.is_dir():
            paths.extend(root.glob(pattern))
    return tuple(sorted(paths, key=lambda path: path.stat().st_mtime))


def _response_payload(line: str) -> JsonObject | None:
    try:
        record = cast(JsonValue, json.loads(line))
    except json.JSONDecodeError:
        return None
    values = _object_map(record)
    if values is None or values.get("type") != "response_item":
        return None
    return _object_map(values.get("payload"))


def _payload_turn_id(payload: Mapping[str, JsonValue]) -> str | None:
    metadata = _object_map(payload.get("internal_chat_message_metadata_passthrough"))
    if metadata is None:
        return None
    turn_id = metadata.get("turn_id")
    return turn_id if isinstance(turn_id, str) else None


def _probe_integrity_ok(plugin_root: Path) -> bool:
    try:
        source = (plugin_root / PROBE_RELATIVE_PATH).read_bytes()
    except OSError:
        return False
    canonical = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest() == EXPECTED_PROBE_SHA256


def _single_valid_evidence(raw: JsonValue) -> JsonObject | None:
    candidates = tuple(_valid_evidence_objects(raw))
    return candidates[0] if len(candidates) == 1 else None


def _valid_evidence_objects(raw: JsonValue) -> Iterable[JsonObject]:
    decoder = json.JSONDecoder()
    for text in _string_values(raw):
        for match in re.finditer(r"\{", text):
            try:
                value, _ = cast(
                    tuple[JsonValue, int], decoder.raw_decode(text[match.start() :])
                )
            except json.JSONDecodeError:
                continue
            evidence = _object_map(value)
            if evidence is not None and _valid_evidence(evidence):
                yield evidence


def _string_values(raw: JsonValue) -> Iterable[str]:
    if isinstance(raw, str):
        yield raw
        return
    values = _object_map(raw)
    if values is not None:
        for value in values.values():
            yield from _string_values(value)
        return
    if isinstance(raw, list):
        for value in raw:
            yield from _string_values(value)


def _object_map(raw: JsonValue) -> JsonObject | None:
    return raw if isinstance(raw, dict) else None


def _valid_evidence(evidence: Mapping[str, JsonValue]) -> bool:
    if (
        evidence.get("protocol") != PROTOCOL
        or evidence.get("browser_type") != "chrome"
        or evidence.get("connector_name") != CONNECTOR_NAME
        or evidence.get("connector_path") != CONNECTOR_PATH
    ):
        return False
    status = evidence.get("status")
    failed_stage = evidence.get("failed_stage")
    verified = (
        status == "verified"
        and evidence.get("chat_mode") == "chat"
        and evidence.get("pro_mode") is True
        and evidence.get("action") in {"attached", "already_attached"}
        and "failed_stage" not in evidence
    )
    failed = (
        status == "failed"
        and evidence.get("chat_mode") == "unverified"
        and evidence.get("pro_mode") is False
        and evidence.get("action") == "none"
        and isinstance(failed_stage, str)
        and bool(failed_stage)
    )
    return verified or failed
