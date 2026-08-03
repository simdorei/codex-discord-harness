from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from datetime import datetime
from typing import TypeVar

from codex_pro_runtime_observation_models import (
    BrowserObservation,
    PostRestartObservation,
    RuntimeObservation,
    RuntimeObservationBase,
    RuntimeObservationRelease,
    TerminalObservation,
    ToolExposureObservation,
)
from codex_pro_runtime_receipt_models import TerminalToolAction, TerminalToolName

ObservationT = TypeVar("ObservationT", bound=RuntimeObservationBase)


class RuntimeObservationAuthority:
    """Mints process-bound observations without persisting its random key."""

    def __init__(self) -> None:
        self.__lock = threading.Lock()
        self.__key = secrets.token_bytes(32)

    def begin_cycle(self) -> None:
        """Invalidate observations minted for an earlier collector cycle."""
        with self.__lock:
            self.__key = secrets.token_bytes(32)

    def cycle_binding_sha256(self) -> str:
        """Return an opaque binding without exposing the process-only cycle key."""
        with self.__lock:
            return hmac.new(
                self.__key,
                b"runtime-observation-cycle-v1",
                hashlib.sha256,
            ).hexdigest()

    def post_restart(
        self,
        release: RuntimeObservationRelease,
        *,
        evidence_sha256: str,
        recorded_at: datetime,
        resident_generation: int,
        resident_started_at: datetime,
        plugin_fingerprint_sha256: str,
        browser_plugin_version: str,
    ) -> PostRestartObservation:
        return self._mint(
            PostRestartObservation,
            {
                "release": release,
                "evidence_sha256": evidence_sha256,
                "recorded_at": recorded_at,
                "resident_generation": resident_generation,
                "resident_started_at": resident_started_at,
                "plugin_fingerprint_sha256": plugin_fingerprint_sha256,
                "browser_plugin_version": browser_plugin_version,
            },
        )

    def browser(
        self,
        release: RuntimeObservationRelease,
        *,
        evidence_sha256: str,
        recorded_at: datetime,
    ) -> BrowserObservation:
        return self._mint(
            BrowserObservation,
            {
                "release": release,
                "evidence_sha256": evidence_sha256,
                "recorded_at": recorded_at,
            },
        )

    def tool_exposure(
        self,
        release: RuntimeObservationRelease,
        *,
        evidence_sha256: str,
        recorded_at: datetime,
        session_binding_sha256: str,
    ) -> ToolExposureObservation:
        return self._mint(
            ToolExposureObservation,
            {
                "release": release,
                "evidence_sha256": evidence_sha256,
                "recorded_at": recorded_at,
                "session_binding_sha256": session_binding_sha256,
            },
        )

    def terminal(
        self,
        release: RuntimeObservationRelease,
        *,
        evidence_sha256: str,
        recorded_at: datetime,
        session_binding_sha256: str,
        tool_name: TerminalToolName,
        action: TerminalToolAction,
        observation_bound: bool,
        observation_sha256: str,
    ) -> TerminalObservation:
        return self._mint(
            TerminalObservation,
            {
                "release": release,
                "evidence_sha256": evidence_sha256,
                "recorded_at": recorded_at,
                "session_binding_sha256": session_binding_sha256,
                "tool_name": tool_name,
                "action": action,
                "observation_bound": observation_bound,
                "observation_sha256": observation_sha256,
            },
        )

    def verifies(self, observation: RuntimeObservation) -> bool:
        expected = self._observation_id(
            observation.model_dump(mode="json", exclude={"observation_id"})
        )
        return hmac.compare_digest(observation.observation_id, expected)

    def _mint(
        self,
        model: type[ObservationT],
        raw_values: dict[str, object],
    ) -> ObservationT:
        validated = model.model_validate(
            {"observation_id": "rtobs_" + "0" * 64, **raw_values}
        )
        payload = validated.model_dump(mode="json", exclude={"observation_id"})
        return model.model_validate(
            {"observation_id": self._observation_id(payload), **payload}
        )

    def _observation_id(self, payload: object) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with self.__lock:
            digest = hmac.new(self.__key, canonical, hashlib.sha256).hexdigest()
        return f"rtobs_{digest}"


__all__ = ["RuntimeObservationAuthority"]
