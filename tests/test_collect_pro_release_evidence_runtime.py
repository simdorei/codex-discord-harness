from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import collect_pro_release_evidence as cli
from codex_pro_release_evidence import ProReleaseEvidence
from codex_pro_runtime_receipt_io import write_runtime_receipts
from tests.pro_runtime_receipt_support import (
    complete_runtime_receipts,
    publish_current_resident_identity,
    ready_release_evidence,
)


def test_cli_derives_ready_from_complete_runtime_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_current_release_evidence",
        _ready_evidence,
    )
    receipts_path = tmp_path / "runtime.json"
    output_path = tmp_path / "release.json"
    _ = write_runtime_receipts(complete_runtime_receipts(), receipts_path)
    _ = publish_current_resident_identity(tmp_path)

    exit_code = cli.main(
        (
            "--repo-root",
            str(tmp_path),
            "--runtime-receipts",
            str(receipts_path),
            "--output",
            str(output_path),
        )
    )
    payload = _payload(output_path)

    assert exit_code == 0
    assert payload["certification_scope"] == "release"
    assert payload["release_ready"] is True
    assert payload["release_blockers"] == []


def test_cli_preserves_pre_restart_mode_without_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_current_release_evidence",
        _ready_evidence,
    )
    output_path = tmp_path / "pre.json"

    exit_code = cli.main(
        (
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output_path),
        )
    )
    payload = _payload(output_path)

    assert exit_code == 0
    assert payload["certification_scope"] == "pre_restart"
    assert payload["release_ready"] is False
    assert payload["deferred_check_ids"]


def test_cli_surfaces_malformed_receipts_without_writing_release_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_current_release_evidence",
        _ready_evidence,
    )
    receipts_path = tmp_path / "malformed.json"
    output_path = tmp_path / "should-not-exist.json"
    _ = receipts_path.write_text('{"schema_version":1}', encoding="utf-8")

    exit_code = cli.main(
        (
            "--repo-root",
            str(tmp_path),
            "--runtime-receipts",
            str(receipts_path),
            "--output",
            str(output_path),
        )
    )

    assert exit_code == 2
    assert "Runtime receipts invalid" in capsys.readouterr().err
    assert not output_path.exists()


def test_cli_fails_closed_when_live_resident_identity_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "collect_current_release_evidence",
        _ready_evidence,
    )
    receipts_path = tmp_path / "runtime.json"
    output_path = tmp_path / "should-not-exist.json"
    _ = write_runtime_receipts(complete_runtime_receipts(), receipts_path)

    exit_code = cli.main(
        (
            "--repo-root",
            str(tmp_path),
            "--runtime-receipts",
            str(receipts_path),
            "--output",
            str(output_path),
        )
    )

    assert exit_code == 2
    assert "Resident identity invalid" in capsys.readouterr().err
    assert not output_path.exists()


def _ready_evidence(
    _root: Path,
    *,
    current_resident=None,
) -> ProReleaseEvidence:
    return replace(
        ready_release_evidence(),
        resident_identity=current_resident,
    )


def _payload(path: Path) -> dict[str, object]:
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)
