from __future__ import annotations

import threading
from datetime import datetime
from typing import final

from codex_pro_runtime_observation_authority import RuntimeObservationAuthority
from codex_pro_runtime_observation_models import (
    BrowserObservation,
    PostRestartObservation,
    RuntimeObservation,
    RuntimeObservationPhase,
    RuntimeObservationRelease,
    RuntimeObservationSnapshot,
    TerminalObservation,
    ToolExposureObservation,
)

_TERMINAL_SEQUENCE = (
    "terminal_window_capture",
    "terminal_window_type",
    "terminal_window_capture",
    "terminal_window_keys",
    "terminal_window_capture",
    "terminal_window_interrupt",
)


@final
class RuntimeObservationCollector:  # MUTABLE_OK: synchronized state machine.
    """Tracks live provenance in memory without creating release receipts."""

    def __init__(self, authority: RuntimeObservationAuthority) -> None:
        self._authority = authority
        self._lock = threading.Lock()
        self._authority.begin_cycle()
        self._reset_locked()

    def observe_post_restart(
        self, observation: PostRestartObservation
    ) -> RuntimeObservationSnapshot:
        return self._observe(observation)

    def observe_browser(
        self, observation: BrowserObservation
    ) -> RuntimeObservationSnapshot:
        return self._observe(observation)

    def observe_tool_exposure(
        self, observation: ToolExposureObservation
    ) -> RuntimeObservationSnapshot:
        return self._observe(observation)

    def observe_terminal(
        self, observation: TerminalObservation
    ) -> RuntimeObservationSnapshot:
        return self._observe(observation)

    def snapshot(self) -> RuntimeObservationSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def invalidate(self, failure_code: str) -> RuntimeObservationSnapshot:
        with self._lock:
            if self._phase is RuntimeObservationPhase.INVALID:
                return self._snapshot_locked()
            return self._invalidate_locked(failure_code)

    def reset(self) -> RuntimeObservationSnapshot:
        with self._lock:
            self._authority.begin_cycle()
            self._reset_locked()
            return self._snapshot_locked()

    def _observe(self, observation: RuntimeObservation) -> RuntimeObservationSnapshot:
        with self._lock:
            if self._phase is RuntimeObservationPhase.INVALID:
                return self._snapshot_locked()
            if not self._authority.verifies(observation):
                return self._invalidate_locked("provenance_invalid")
            previous = self._observations.get(observation.observation_id)
            if previous is not None:
                if previous != observation:
                    return self._invalidate_locked("observation_id_reused")
                return self._snapshot_locked()
            if not self._context_matches_locked(observation):
                return self._invalidate_locked("release_context_changed")
            if (
                self._last_recorded_at is not None
                and observation.recorded_at <= self._last_recorded_at
            ):
                return self._invalidate_locked("observation_time_not_increasing")
            if not self._expected_locked(observation):
                return self._invalidate_locked("observation_out_of_order")
            self._observations[observation.observation_id] = observation
            self._last_recorded_at = observation.recorded_at
            self._advance_locked(observation)
            return self._snapshot_locked()

    def _expected_locked(self, observation: RuntimeObservation) -> bool:
        if self._phase is RuntimeObservationPhase.EMPTY:
            return isinstance(observation, PostRestartObservation)
        if self._phase is RuntimeObservationPhase.WAITING_BROWSER:
            return isinstance(observation, BrowserObservation)
        if self._phase is RuntimeObservationPhase.WAITING_TOOL_EXPOSURE:
            return isinstance(observation, ToolExposureObservation)
        if self._phase is RuntimeObservationPhase.WAITING_TERMINAL:
            if (
                not isinstance(observation, TerminalObservation)
                or observation.session_binding_sha256 != self._session_binding
                or observation.tool_name
                != _TERMINAL_SEQUENCE[self._terminal_progress]
            ):
                return False
            if observation.tool_name == "terminal_window_capture":
                return self._pending_terminal_observation is None
            return (
                self._pending_terminal_observation is not None
                and observation.observation_sha256
                == self._pending_terminal_observation
            )
        return False

    def _advance_locked(self, observation: RuntimeObservation) -> None:
        if isinstance(observation, PostRestartObservation):
            self._release = observation.release
            self._phase = RuntimeObservationPhase.WAITING_BROWSER
        elif isinstance(observation, BrowserObservation):
            self._phase = RuntimeObservationPhase.WAITING_TOOL_EXPOSURE
        elif isinstance(observation, ToolExposureObservation):
            self._session_binding = observation.session_binding_sha256
            self._phase = RuntimeObservationPhase.WAITING_TERMINAL
        else:
            if observation.tool_name == "terminal_window_capture":
                self._pending_terminal_observation = observation.observation_sha256
            else:
                self._pending_terminal_observation = None
            self._terminal_progress += 1
            if self._terminal_progress == len(_TERMINAL_SEQUENCE):
                self._phase = RuntimeObservationPhase.READY_TO_EMIT

    def _context_matches_locked(self, observation: RuntimeObservation) -> bool:
        return self._release is None or observation.release == self._release

    def _invalidate_locked(self, failure_code: str) -> RuntimeObservationSnapshot:
        self._phase = RuntimeObservationPhase.INVALID
        self._failure_code = failure_code
        return self._snapshot_locked()

    def _snapshot_locked(self) -> RuntimeObservationSnapshot:
        return RuntimeObservationSnapshot(
            phase=self._phase,
            observed_count=len(self._observations),
            terminal_progress=self._terminal_progress,
            ready=self._phase is RuntimeObservationPhase.READY_TO_EMIT,
            failure_code=self._failure_code,
            last_recorded_at=self._last_recorded_at,
        )

    def _reset_locked(self) -> None:
        self._phase = RuntimeObservationPhase.EMPTY
        self._release: RuntimeObservationRelease | None = None
        self._session_binding: str | None = None
        self._terminal_progress = 0
        self._pending_terminal_observation: str | None = None
        self._failure_code: str | None = None
        self._last_recorded_at: datetime | None = None
        self._observations: dict[str, RuntimeObservation] = {}


__all__ = ["RuntimeObservationCollector"]
