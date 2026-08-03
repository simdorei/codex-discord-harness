from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_pro_browser_evidence_source import BrowserEvidenceSourceError
from codex_pro_runtime_observation_runtime import (
    RuntimeCycleStart,
    RuntimeObservationRuntime,
    RuntimeObservationStartError,
)
from codex_pro_runtime_preflight import ProRuntimeStatus
from tests.test_browser_evidence_hook import load_hook, post_payload

_REVISION = "a" * 40
_FINGERPRINT = "b" * 64
_NOW = datetime.now(UTC)


def test_matching_release_starts_post_restart_cycle_without_emitting_receipts() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        runtime = _runtime(root, root / "evidence")

        result = runtime.start_cycle(_status(), root, "session-a")

        snapshot = runtime.snapshot()
        assert result is RuntimeCycleStart.STARTED
        assert snapshot.phase == "waiting_browser"
        assert snapshot.observed_count == 1
        assert snapshot.terminal_progress == 0
        assert tuple((root / "evidence").glob("*")) == ()


def test_other_repository_is_not_applicable_and_does_not_reset_cycle() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        other = root / "other"
        other.mkdir()
        runtime = _runtime(root, root / "evidence", other_repo=other)
        assert runtime.start_cycle(_status(), root, "session-a") == "started"
        before = runtime.snapshot()

        result = runtime.start_cycle(_status(), other, "session-b")

        assert result is RuntimeCycleStart.NOT_APPLICABLE
        assert runtime.snapshot() == before


def test_resident_generation_drift_fails_before_resetting_existing_cycle() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current_generation = [7]
        runtime = _runtime(
            root,
            root / "evidence",
            generation_reader=lambda: current_generation[0],
        )
        _ = runtime.start_cycle(_status(), root, "session-a")
        before = runtime.snapshot()
        current_generation[0] = 8

        with pytest.raises(RuntimeObservationStartError, match="changed"):
            _ = runtime.start_cycle(_status(), root, "session-a")

        assert runtime.snapshot() == before


def test_accepting_time_or_fingerprint_drift_fails_before_reset() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        accepting = [(_NOW - timedelta(seconds=10)).timestamp()]
        fingerprint = [_FINGERPRINT]
        runtime = _runtime(
            root,
            root / "evidence",
            accepting_reader=lambda: accepting[0],
            fingerprint_reader=lambda: fingerprint[0],
        )
        _ = runtime.start_cycle(_status(), root, "session-a")
        before = runtime.snapshot()

        accepting[0] += 1
        with pytest.raises(RuntimeObservationStartError, match="changed"):
            _ = runtime.start_cycle(_status(), root, "session-a")
        assert runtime.snapshot() == before

        accepting[0] = _status().resident_accepting_since
        fingerprint[0] = "c" * 64
        with pytest.raises(RuntimeObservationStartError, match="changed"):
            _ = runtime.start_cycle(_status(), root, "session-a")
        assert runtime.snapshot() == before


def test_revision_change_during_start_fails_before_creating_cycle() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        revisions = iter((_REVISION, "d" * 40))
        runtime = _runtime(
            root,
            root / "evidence",
            revision_reader=lambda: next(revisions),
        )

        with pytest.raises(RuntimeObservationStartError, match="repository changed"):
            _ = runtime.start_cycle(_status(), root, "session-a")

        assert runtime.snapshot().phase == "empty"


def test_latest_successful_cycle_owns_browser_session_binding() -> None:
    hook = load_hook()
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        data_dir = root / "plugin-data"
        evidence_dir = data_dir / "browser-evidence"
        runtime = _runtime(root, evidence_dir)
        _ = runtime.start_cycle(_status(), root, "session-a")
        _ = runtime.start_cycle(_status(), root, "session-b")

        old_payload = post_payload(hook, "available")
        assert hook.process_post_tool_use(
            old_payload,
            data_dir,
            clock=lambda: _NOW + timedelta(seconds=1),
        )
        with pytest.raises(BrowserEvidenceSourceError, match="session binding"):
            _ = runtime.read_browser_proof()

        new_payload = post_payload(hook, "available")
        new_payload["session_id"] = "session-b"
        new_payload["turn_id"] = "turn-b"
        new_payload["tool_use_id"] = "tool-b"
        assert hook.process_post_tool_use(
            new_payload,
            data_dir,
            clock=lambda: _NOW + timedelta(seconds=2),
        )
        proof = runtime.read_browser_proof()

        assert proof.recorded_at == _NOW + timedelta(seconds=2)
        assert runtime.snapshot().phase == "waiting_browser"
        assert runtime.snapshot().observed_count == 1


def test_naive_clock_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        runtime = _runtime(
            root,
            root / "evidence",
            clock=lambda: _NOW.replace(tzinfo=None),
        )

        with pytest.raises(RuntimeObservationStartError, match="naive"):
            _ = runtime.start_cycle(_status(), root, "session-a")

        assert runtime.snapshot().phase == "empty"


def _status() -> ProRuntimeStatus:
    return ProRuntimeStatus(
        remote_plugin_version="remote-1",
        browser_plugin_version="browser-1",
        resident_generation=7,
        resident_accepting_since=(_NOW - timedelta(seconds=10)).timestamp(),
        resident_plugin_fingerprint=_FINGERPRINT,
    )


def _runtime(
    root: Path,
    evidence_dir: Path,
    *,
    other_repo: Path | None = None,
    generation_reader: Callable[[], int] = lambda: 7,
    accepting_reader: Callable[[], float] = lambda: (
        _NOW - timedelta(seconds=10)
    ).timestamp(),
    fingerprint_reader: Callable[[], str] = lambda: _FINGERPRINT,
    revision_reader: Callable[[], str] = lambda: _REVISION,
    clock: Callable[[], datetime] = lambda: _NOW,
) -> RuntimeObservationRuntime:
    common = (root / ".git-common").resolve()
    other_common = (root / ".git-other").resolve()

    def git_runner(command: Sequence[str], cwd: Path) -> tuple[int, str]:
        value = command[-1]
        if value == "--git-common-dir":
            return 0, str(other_common if cwd == other_repo else common)
        if value == "HEAD":
            return 0, revision_reader()
        return 1, ""

    def snapshot() -> AppServerLifecycleSnapshot:
        return AppServerLifecycleSnapshot(
            generation=generation_reader(),
            healthy=True,
            accepting_since=accepting_reader(),
            plugin_runtime_fingerprint=fingerprint_reader(),
        )

    return RuntimeObservationRuntime(
        release_repo_root=root,
        snapshot_reader=snapshot,
        evidence_dir=evidence_dir,
        clock=clock,
        git_runner=git_runner,
    )
