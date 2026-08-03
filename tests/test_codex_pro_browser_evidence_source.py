from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from codex_pro_browser_evidence_source import (
    BrowserEvidenceSourceError,
    browser_session_binding_sha256,
    read_latest_browser_evidence,
)
from tests.test_browser_evidence_hook import load_hook, post_payload


def test_reads_latest_available_hook_source_without_exposing_raw_ids() -> None:
    hook = load_hook()
    recorded_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        assert hook.process_post_tool_use(
            post_payload(hook, "available"),
            data_dir,
            clock=lambda: recorded_at,
        )

        proof = read_latest_browser_evidence(
            data_dir / "browser-evidence",
            expected_session_binding_sha256=browser_session_binding_sha256(
                "session-a"
            ),
            not_before=recorded_at - timedelta(seconds=1),
            now=recorded_at + timedelta(seconds=1),
        )

        payload = proof.model_dump(mode="json")
        assert proof.recorded_at == recorded_at
        assert set(payload) == {
            "recorded_at",
            "evidence_sha256",
            "source_binding_sha256",
        }
        assert "session-a" not in json.dumps(payload)
        assert "turn-a" not in json.dumps(payload)
        assert "tool-a" not in json.dumps(payload)


def test_latest_malformed_source_fails_closed_instead_of_using_older_valid() -> None:
    hook = load_hook()
    recorded_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as raw_dir:
        evidence_dir = Path(raw_dir) / "browser-evidence"
        assert hook.process_post_tool_use(
            post_payload(hook, "available"),
            Path(raw_dir),
            clock=lambda: recorded_at,
        )
        malformed = evidence_dir / ("f" * 64 + ".json")
        _ = malformed.write_text("{bad-json", encoding="utf-8")
        future_mtime = recorded_at.timestamp() + 2
        os.utime(malformed, (future_mtime, future_mtime))

        with pytest.raises(BrowserEvidenceSourceError, match="malformed"):
            _ = read_latest_browser_evidence(
                evidence_dir,
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "session-a"
                ),
                not_before=recorded_at - timedelta(seconds=1),
                now=recorded_at + timedelta(seconds=3),
            )


@pytest.mark.parametrize("status", ["unavailable", "unverified"])
def test_non_available_browser_status_is_rejected(status: str) -> None:
    hook = load_hook()
    recorded_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        assert hook.process_post_tool_use(
            post_payload(hook, status),
            data_dir,
            clock=lambda: recorded_at,
        )

        with pytest.raises(BrowserEvidenceSourceError, match="not proven available"):
            _ = read_latest_browser_evidence(
                data_dir / "browser-evidence",
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "session-a"
                ),
                not_before=recorded_at - timedelta(seconds=1),
                now=recorded_at + timedelta(seconds=1),
            )


def test_wrong_session_and_stale_source_are_rejected() -> None:
    hook = load_hook()
    recorded_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        assert hook.process_post_tool_use(
            post_payload(hook, "available"),
            data_dir,
            clock=lambda: recorded_at,
        )
        evidence_dir = data_dir / "browser-evidence"

        with pytest.raises(BrowserEvidenceSourceError, match="session binding"):
            _ = read_latest_browser_evidence(
                evidence_dir,
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "another-session"
                ),
                not_before=recorded_at - timedelta(seconds=1),
                now=recorded_at + timedelta(seconds=1),
            )
        with pytest.raises(BrowserEvidenceSourceError, match="stale"):
            _ = read_latest_browser_evidence(
                evidence_dir,
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "session-a"
                ),
                not_before=recorded_at,
                now=recorded_at + timedelta(seconds=1),
            )


def test_filename_and_recorded_time_contracts_are_strict() -> None:
    hook = load_hook()
    recorded_at = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as raw_dir:
        data_dir = Path(raw_dir)
        assert hook.process_post_tool_use(
            post_payload(hook, "available"),
            data_dir,
            clock=lambda: recorded_at,
        )
        evidence_dir = data_dir / "browser-evidence"
        source_path = next(evidence_dir.glob("*.json"))
        source = cast(
            dict[str, object],
            json.loads(source_path.read_text(encoding="utf-8")),
        )
        source["recorded_at"] = recorded_at.replace(tzinfo=None).isoformat()
        _ = source_path.write_text(json.dumps(source), encoding="utf-8")

        with pytest.raises(BrowserEvidenceSourceError, match="malformed"):
            _ = read_latest_browser_evidence(
                evidence_dir,
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "session-a"
                ),
                not_before=recorded_at - timedelta(seconds=1),
                now=recorded_at + timedelta(seconds=1),
            )

        source["recorded_at"] = recorded_at.isoformat()
        wrong_path = evidence_dir / ("e" * 64 + ".json")
        _ = wrong_path.write_text(json.dumps(source), encoding="utf-8")
        source_path.unlink()
        with pytest.raises(BrowserEvidenceSourceError, match="filename binding"):
            _ = read_latest_browser_evidence(
                evidence_dir,
                expected_session_binding_sha256=browser_session_binding_sha256(
                    "session-a"
                ),
                not_before=recorded_at - timedelta(seconds=1),
                now=recorded_at + timedelta(seconds=1),
            )
