from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import ClassVar, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from codex_process_runtime_identity import process_identity
from codex_pro_runtime_preflight import ProRuntimeStatus
from codex_pro_runtime_receipt_models import EXPECTED_BRIDGE_PROTOCOL_VERSION

DEFAULT_RESIDENT_IDENTITY_PATH = Path(
    ".codex-remote-mcp/pro-resident-runtime.json"
)
DEFAULT_RESIDENT_IDENTITY_KEY_PATH = Path(
    ".codex-remote-mcp/pro-resident-runtime.key"
)
_KEY_BYTES = 32


class ResidentIdentityError(ValueError):
    @override
    def __str__(self) -> str:
        return self.args[0] if self.args else "resident identity is invalid"


class ResidentRuntimeIdentity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    process_id: int = Field(gt=0)
    process_identity: str = Field(min_length=3, max_length=200)
    resident_generation: int = Field(ge=1)
    resident_started_at: AwareDatetime
    plugin_fingerprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    remote_plugin_version: str = Field(min_length=1, max_length=100)
    browser_plugin_version: str = Field(min_length=1, max_length=100)
    protocol_version: int = Field(
        default=EXPECTED_BRIDGE_PROTOCOL_VERSION,
        ge=EXPECTED_BRIDGE_PROTOCOL_VERSION,
        le=EXPECTED_BRIDGE_PROTOCOL_VERSION,
    )
    recorded_at: AwareDatetime
    identity_hmac_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def build_resident_identity(
    runtime_status: ProRuntimeStatus,
    *,
    recorded_at: datetime,
    key: bytes,
) -> ResidentRuntimeIdentity:
    process_id = runtime_status.resident_process_id
    process_token = runtime_status.resident_process_identity
    if process_id is None or process_token is None:
        raise ResidentIdentityError("resident process identity is unavailable")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "process_id": process_id,
        "process_identity": process_token,
        "resident_generation": runtime_status.resident_generation,
        "resident_started_at": datetime.fromtimestamp(
            runtime_status.resident_accepting_since,
            recorded_at.tzinfo,
        ),
        "plugin_fingerprint_sha256": (
            runtime_status.resident_plugin_fingerprint
        ),
        "remote_plugin_version": runtime_status.remote_plugin_version,
        "browser_plugin_version": runtime_status.browser_plugin_version,
        "protocol_version": EXPECTED_BRIDGE_PROTOCOL_VERSION,
        "recorded_at": recorded_at,
    }
    candidate = ResidentRuntimeIdentity.model_validate(
        {
            **unsigned,
            "identity_hmac_sha256": "0" * 64,
        }
    )
    canonical = candidate.model_dump(
        mode="json",
        exclude={"identity_hmac_sha256"},
    )
    return candidate.model_copy(
        update={"identity_hmac_sha256": _sign(canonical, key)}
    )


def publish_resident_identity(
    identity: ResidentRuntimeIdentity,
    path: Path,
    key_path: Path,
) -> Path:
    key = load_or_create_identity_key(key_path)
    _verify_signature(identity, key)
    payload = identity.model_dump_json(indent=2) + "\n"
    _atomic_write(path, payload.encode("utf-8"))
    return path


def read_current_resident_identity(
    path: Path,
    key_path: Path,
) -> ResidentRuntimeIdentity:
    try:
        identity = ResidentRuntimeIdentity.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        key = _read_identity_key(key_path)
        _verify_signature(identity, key)
        actual = process_identity(identity.process_id)
    except (OSError, ValidationError) as exc:
        raise ResidentIdentityError("current resident identity is unavailable") from exc
    if not hmac.compare_digest(actual, identity.process_identity):
        raise ResidentIdentityError("current resident process changed")
    return identity


def load_or_create_identity_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _read_identity_key(path)
    except FileNotFoundError:
        key = secrets.token_bytes(_KEY_BYTES)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return _read_identity_key(path)
        with os.fdopen(descriptor, "wb") as target:
            _ = target.write(key)
            target.flush()
            os.fsync(target.fileno())
        return key


def remove_resident_identity(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ResidentIdentityError("resident identity could not be cleared") from exc


def _read_identity_key(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) != _KEY_BYTES:
        raise ResidentIdentityError("resident identity key is invalid")
    return key


def _verify_signature(identity: ResidentRuntimeIdentity, key: bytes) -> None:
    unsigned = identity.model_dump(
        mode="json",
        exclude={"identity_hmac_sha256"},
    )
    expected = _sign(unsigned, key)
    if not hmac.compare_digest(expected, identity.identity_hmac_sha256):
        raise ResidentIdentityError("resident identity authentication failed")


def _sign(value: object, key: bytes) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as target:
            _ = target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        _ = temp.replace(path)
    finally:
        _ = temp.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_RESIDENT_IDENTITY_KEY_PATH",
    "DEFAULT_RESIDENT_IDENTITY_PATH",
    "ResidentIdentityError",
    "ResidentRuntimeIdentity",
    "build_resident_identity",
    "load_or_create_identity_key",
    "publish_resident_identity",
    "read_current_resident_identity",
    "remove_resident_identity",
]
