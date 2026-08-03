from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codex_pro_runtime_observation_authority import RuntimeObservationAuthority
from codex_pro_runtime_observation_collector import RuntimeObservationCollector
from codex_pro_runtime_observation_models import (
    RuntimeObservationPhase,
    RuntimeObservationRelease,
)
from codex_pro_runtime_receipts import evaluate_runtime_receipts
from codex_pro_runtime_receipt_models import TerminalToolAction, TerminalToolName

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_REVISION = "d" * 40
_START = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
_SEQUENCE: tuple[tuple[TerminalToolName, TerminalToolAction, bool], ...] = (
    ("terminal_window_capture", "capture", False),
    ("terminal_window_type", "type", True),
    ("terminal_window_capture", "capture", False),
    ("terminal_window_keys", "keys", True),
    ("terminal_window_capture", "capture", False),
    ("terminal_window_interrupt", "interrupt", True),
)


def test_complete_observation_sequence_stays_in_memory_and_release_fails_closed() -> None:
    authority, collector, release = _runtime()
    restart = authority.post_restart(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
        resident_generation=2,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_B,
        browser_plugin_version="browser-1",
    )
    assert collector.observe_post_restart(restart).phase == "waiting_browser"
    assert collector.observe_browser(
        authority.browser(release, evidence_sha256=_SHA_B, recorded_at=_at(2))
    ).phase == "waiting_tool_exposure"
    assert collector.observe_tool_exposure(
        authority.tool_exposure(
            release,
            evidence_sha256=_SHA_C,
            recorded_at=_at(3),
            session_binding_sha256=_SHA_A,
        )
    ).phase == "waiting_terminal"
    snapshot = collector.snapshot()
    pending_observation_sha256: str | None = None
    for index, (tool_name, action, bound) in enumerate(_SEQUENCE, start=4):
        observation_sha256 = (
            f"{index + 6:x}" * 64
            if action == "capture"
            else pending_observation_sha256
        )
        assert observation_sha256 is not None
        snapshot = collector.observe_terminal(
            authority.terminal(
                release,
                evidence_sha256=f"{index:x}" * 64,
                recorded_at=_at(index),
                session_binding_sha256=_SHA_A,
                tool_name=tool_name,
                action=action,
                observation_bound=bound,
                observation_sha256=observation_sha256,
            )
        )
        pending_observation_sha256 = (
            observation_sha256 if action == "capture" else None
        )
    assert snapshot.phase is RuntimeObservationPhase.READY_TO_EMIT
    assert snapshot.ready
    assert snapshot.observed_count == 9
    assert (
        evaluate_runtime_receipts(
            None,
            repository_revision=_REVISION,
            plugin_version="remote-1",
            pre_restart_ready=True,
        ).ready
        is False
    )


def test_wrong_order_permanently_invalidates_until_reset() -> None:
    authority, collector, release = _runtime()
    browser = authority.browser(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
    )
    invalid = collector.observe_browser(browser)
    assert invalid.phase is RuntimeObservationPhase.INVALID
    assert invalid.failure_code == "observation_out_of_order"

    restart = authority.post_restart(
        release,
        evidence_sha256=_SHA_B,
        recorded_at=_at(2),
        resident_generation=1,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_C,
        browser_plugin_version="browser-1",
    )
    assert collector.observe_post_restart(restart).phase is RuntimeObservationPhase.INVALID
    assert collector.reset().phase is RuntimeObservationPhase.EMPTY


def test_foreign_authority_and_changed_release_context_are_rejected() -> None:
    authority, collector, release = _runtime()
    foreign = RuntimeObservationAuthority()
    restart = foreign.post_restart(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
        resident_generation=1,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_B,
        browser_plugin_version="browser-1",
    )
    assert collector.observe_post_restart(restart).failure_code == "provenance_invalid"

    _ = collector.reset()
    accepted = authority.post_restart(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
        resident_generation=1,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_B,
        browser_plugin_version="browser-1",
    )
    _ = collector.observe_post_restart(accepted)
    changed = release.model_copy(update={"plugin_version": "remote-2"})
    browser = authority.browser(
        changed,
        evidence_sha256=_SHA_C,
        recorded_at=_at(2),
    )
    assert collector.observe_browser(browser).failure_code == "release_context_changed"


def test_duplicate_is_idempotent_but_non_increasing_time_fails() -> None:
    authority, collector, release = _runtime()
    restart = authority.post_restart(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
        resident_generation=1,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_B,
        browser_plugin_version="browser-1",
    )
    first = collector.observe_post_restart(restart)
    duplicate = collector.observe_post_restart(restart)
    assert duplicate == first

    browser = authority.browser(
        release,
        evidence_sha256=_SHA_C,
        recorded_at=_at(1),
    )
    assert (
        collector.observe_browser(browser).failure_code
        == "observation_time_not_increasing"
    )


def test_reset_rejects_observations_from_the_previous_cycle() -> None:
    authority, collector, release = _runtime()
    old_restart = authority.post_restart(
        release,
        evidence_sha256=_SHA_A,
        recorded_at=_at(1),
        resident_generation=1,
        resident_started_at=_START,
        plugin_fingerprint_sha256=_SHA_B,
        browser_plugin_version="browser-1",
    )
    assert collector.observe_post_restart(old_restart).phase == "waiting_browser"

    assert collector.reset().phase is RuntimeObservationPhase.EMPTY
    replayed = collector.observe_post_restart(old_restart)
    assert replayed.phase is RuntimeObservationPhase.INVALID
    assert replayed.failure_code == "provenance_invalid"


def _runtime() -> tuple[
    RuntimeObservationAuthority,
    RuntimeObservationCollector,
    RuntimeObservationRelease,
]:
    authority = RuntimeObservationAuthority()
    release = RuntimeObservationRelease(
        repository_revision=_REVISION,
        plugin_version="remote-1",
        protocol_version=11,
        inventory_sha256=_SHA_A,
    )
    return authority, RuntimeObservationCollector(authority), release


def _at(seconds: int) -> datetime:
    return _START + timedelta(seconds=seconds)
