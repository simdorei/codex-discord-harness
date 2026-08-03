from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from codex_process_runtime_identity import current_process_identity
from codex_pro_resident_identity import (
    ResidentIdentityError,
    build_resident_identity,
    load_or_create_identity_key,
    publish_resident_identity,
    read_current_resident_identity,
)
from codex_pro_runtime_preflight import ProRuntimeStatus


def test_authenticated_live_process_identity_round_trips(tmp_path: Path) -> None:
    identity_path = tmp_path / "resident.json"
    key_path = tmp_path / "resident.key"
    key = load_or_create_identity_key(key_path)
    identity = build_resident_identity(
        _status(),
        recorded_at=datetime.now(UTC),
        key=key,
    )

    _ = publish_resident_identity(identity, identity_path, key_path)

    assert read_current_resident_identity(identity_path, key_path) == identity
    assert len(key) == 32


def test_tampered_or_reused_process_identity_fails_closed(
    tmp_path: Path,
) -> None:
    identity_path = tmp_path / "resident.json"
    key_path = tmp_path / "resident.key"
    key = load_or_create_identity_key(key_path)
    forged = build_resident_identity(
        _status(process_token="1|stale-process"),
        recorded_at=datetime.now(UTC),
        key=key,
    )
    _ = publish_resident_identity(forged, identity_path, key_path)

    with pytest.raises(ResidentIdentityError, match="process changed"):
        _ = read_current_resident_identity(identity_path, key_path)

    raw = identity_path.read_text(encoding="utf-8")
    _ = identity_path.write_text(
        raw.replace('"resident_generation": 7', '"resident_generation": 8'),
        encoding="utf-8",
    )
    with pytest.raises(ResidentIdentityError, match="authentication"):
        _ = read_current_resident_identity(identity_path, key_path)


def _status(
    *,
    process_token: str | None = None,
) -> ProRuntimeStatus:
    import os

    return ProRuntimeStatus(
        remote_plugin_version="remote-1",
        browser_plugin_version="browser-1",
        resident_generation=7,
        resident_accepting_since=datetime(2020, 1, 1, tzinfo=UTC).timestamp(),
        resident_plugin_fingerprint="f" * 64,
        resident_process_id=os.getpid(),
        resident_process_identity=process_token or current_process_identity(),
    )
