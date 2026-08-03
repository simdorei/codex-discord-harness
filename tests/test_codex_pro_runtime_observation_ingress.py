from __future__ import annotations

import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_pro_runtime_observation_ingress import (
    RuntimeIngressStatus,
    TerminalIngressEvidence,
)
from codex_pro_runtime_observation_runtime import RuntimeObservationRuntime
from codex_pro_runtime_preflight import ProRuntimeStatus
from remote_mcp_server.simdorei_mcp.capability_inventory import (
    EXPECTED_TOOL_NAMES,
    build_capability_inventory,
    capability_inventory_sha256,
)
from simdorei_mcp_common.runtime_provenance import ObservedTerminalTool
from tests.test_browser_evidence_hook import load_hook, post_payload

_NOW = datetime.now(UTC)
_REVISION = "a" * 40
_FINGERPRINT = "b" * 64
_SESSION_BINDING = "c" * 64


def test_browser_capability_and_terminal_sequence_reaches_ready_to_emit() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        cycle_binding = _advance_to_terminal(runtime, root, current)

        captures = ("twobs_" + "a" * 16, "twobs_" + "b" * 16, "twobs_" + "c" * 16)
        sequence: tuple[tuple[ObservedTerminalTool, str, str | None], ...] = (
            ("terminal_window_capture", captures[0], None),
            ("terminal_window_type", captures[0], captures[0]),
            ("terminal_window_capture", captures[1], None),
            ("terminal_window_keys", captures[1], captures[1]),
            ("terminal_window_capture", captures[2], None),
            ("terminal_window_interrupt", captures[2], captures[2]),
        )
        for tool_name, observation_id, expected_id in sequence:
            current[0] += timedelta(seconds=1)
            status = runtime.observe_terminal(
                thread_id="session-a",
                computer_session_id="computer-a",
                session_binding_sha256=_SESSION_BINDING,
                cycle_binding_sha256=cycle_binding,
                evidence=TerminalIngressEvidence(
                    tool_name=tool_name,
                    observation_id=observation_id,
                    expected_observation_id=expected_id,
                    identity_digest="d" * 64,
                    recorded_at=current[0],
                ),
            )
            assert status is RuntimeIngressStatus.ACCEPTED

        snapshot = runtime.snapshot()
        assert snapshot.phase == "ready_to_emit"
        assert snapshot.ready is True
        assert snapshot.observed_count == 9


def test_wrong_route_invalidates_the_active_release_cycle() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        cycle_binding = _advance_to_terminal(runtime, root, current)
        current[0] += timedelta(seconds=1)

        status = runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="wrong-computer",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=TerminalIngressEvidence(
                tool_name="terminal_window_capture",
                observation_id="twobs_" + "a" * 16,
                identity_digest="d" * 64,
                recorded_at=current[0],
            ),
        )

        assert status is RuntimeIngressStatus.REJECTED
        assert runtime.snapshot().phase == "invalid"


def test_terminal_action_must_consume_the_immediately_preceding_capture() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        cycle_binding = _advance_to_terminal(runtime, root, current)
        capture_id = "twobs_" + "a" * 16
        current[0] += timedelta(seconds=1)
        assert runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=TerminalIngressEvidence(
                tool_name="terminal_window_capture",
                observation_id=capture_id,
                identity_digest="d" * 64,
                recorded_at=current[0],
            ),
        ) == "accepted"
        current[0] += timedelta(seconds=1)

        status = runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=TerminalIngressEvidence(
                tool_name="terminal_window_type",
                observation_id=capture_id,
                expected_observation_id="twobs_" + "f" * 16,
                identity_digest="d" * 64,
                recorded_at=current[0],
            ),
        )

        assert status is RuntimeIngressStatus.REJECTED
        assert runtime.snapshot().phase == "invalid"


def test_late_previous_cycle_binding_cannot_contaminate_a_new_cycle() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        old_binding = _advance_to_terminal(runtime, root, current)
        current[0] += timedelta(seconds=1)
        _ = runtime.start_cycle(_status(), root, "session-a")
        new_binding = _advance_existing_cycle_to_terminal(runtime, root, current, "second")
        before = runtime.snapshot()
        current[0] += timedelta(seconds=1)

        status = runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=old_binding,
            evidence=TerminalIngressEvidence(
                tool_name="terminal_window_capture",
                observation_id="twobs_" + "e" * 16,
                identity_digest="d" * 64,
                recorded_at=current[0],
            ),
        )

        assert old_binding != new_binding
        assert status is RuntimeIngressStatus.NOT_APPLICABLE
        assert runtime.snapshot() == before


def _advance_to_terminal(
    runtime: RuntimeObservationRuntime,
    root: Path,
    current: list[datetime],
) -> str:
    assert runtime.start_cycle(_status(), root, "session-a") == "started"
    return _advance_existing_cycle_to_terminal(runtime, root, current, "first")


def _advance_existing_cycle_to_terminal(
    runtime: RuntimeObservationRuntime,
    root: Path,
    current: list[datetime],
    suffix: str,
) -> str:
    assert runtime.bind_route(
        thread_id="session-a",
        computer_session_id="computer-a",
        session_binding_sha256=_SESSION_BINDING,
    ) == "accepted"
    hook = load_hook()
    payload = post_payload(hook, "available")
    payload["turn_id"] = f"turn-{suffix}"
    payload["tool_use_id"] = f"tool-{suffix}"
    current[0] += timedelta(seconds=1)
    assert hook.process_post_tool_use(
        payload,
        root / "plugin-data",
        clock=lambda: current[0],
    )
    current[0] += timedelta(seconds=1)
    inventory = build_capability_inventory(EXPECTED_TOOL_NAMES)
    result = runtime.commit_capability(
        thread_id="session-a",
        computer_session_id="computer-a",
        session_binding_sha256=_SESSION_BINDING,
        inventory_sha256=capability_inventory_sha256(inventory),
        tool_count=47,
        terminal_execute_present=True,
        terminal_interact_present=True,
    )
    assert result.status is RuntimeIngressStatus.ACCEPTED
    assert result.cycle_binding_sha256 is not None
    return result.cycle_binding_sha256


def _status() -> ProRuntimeStatus:
    return ProRuntimeStatus(
        remote_plugin_version="remote-1",
        browser_plugin_version="browser-1",
        resident_generation=7,
        resident_accepting_since=(_NOW - timedelta(seconds=10)).timestamp(),
        resident_plugin_fingerprint=_FINGERPRINT,
    )


def _runtime(root: Path, current: list[datetime]) -> RuntimeObservationRuntime:
    common = (root / ".git-common").resolve()

    def git_runner(command: Sequence[str], cwd: Path) -> tuple[int, str]:
        _ = cwd
        if command[-1] == "--git-common-dir":
            return 0, str(common)
        if command[-1] == "HEAD":
            return 0, _REVISION
        return 1, ""

    def snapshot() -> AppServerLifecycleSnapshot:
        return AppServerLifecycleSnapshot(
            generation=7,
            healthy=True,
            accepting_since=(_NOW - timedelta(seconds=10)).timestamp(),
            plugin_runtime_fingerprint=_FINGERPRINT,
        )

    return RuntimeObservationRuntime(
        release_repo_root=root,
        snapshot_reader=snapshot,
        evidence_dir=root / "plugin-data" / "browser-evidence",
        clock=lambda: current[0],
        git_runner=git_runner,
    )
