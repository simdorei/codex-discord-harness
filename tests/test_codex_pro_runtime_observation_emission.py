# pyright: reportPrivateUsage=false
from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from codex_pro_runtime_observation_ingress import (
    RuntimeIngressStatus,
    TerminalIngressEvidence,
)
from codex_pro_runtime_observation_runtime import (
    RuntimeObservationRuntime,
    RuntimeObservationStartError,
)
from codex_pro_runtime_receipt_io import (
    publish_runtime_receipts,
    read_runtime_receipts,
)
from codex_pro_runtime_receipt_models import RuntimeReceiptSet
from codex_pro_runtime_receipts import evaluate_runtime_receipts
from codex_pro_resident_identity import (
    DEFAULT_RESIDENT_IDENTITY_KEY_PATH,
    DEFAULT_RESIDENT_IDENTITY_PATH,
    read_current_resident_identity,
)
from simdorei_mcp_common.runtime_provenance import ObservedTerminalTool
from tests.test_codex_pro_runtime_observation_ingress import (
    _NOW,
    _SESSION_BINDING,
    _advance_to_terminal,
    _runtime,
    _status,
)

_RECEIPT_RELATIVE_PATH = Path(
    ".release-evidence/pro-runtime-receipts.json"
)
_TERMINAL_SEQUENCE: tuple[
    tuple[ObservedTerminalTool, str, str | None], ...
] = (
    ("terminal_window_capture", "twobs_" + "a" * 16, None),
    ("terminal_window_type", "twobs_" + "a" * 16, "twobs_" + "a" * 16),
    ("terminal_window_capture", "twobs_" + "b" * 16, None),
    ("terminal_window_keys", "twobs_" + "b" * 16, "twobs_" + "b" * 16),
    ("terminal_window_capture", "twobs_" + "c" * 16, None),
    (
        "terminal_window_interrupt",
        "twobs_" + "c" * 16,
        "twobs_" + "c" * 16,
    ),
)


def test_ready_cycle_atomically_emits_public_safe_release_receipts() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        cycle_binding = _advance_to_terminal(runtime, root, current)

        _observe(runtime, current, cycle_binding, _TERMINAL_SEQUENCE)

        path = root / _RECEIPT_RELATIVE_PATH
        snapshot = runtime.snapshot()
        receipts = read_runtime_receipts(path)
        resident = read_current_resident_identity(
            root / DEFAULT_RESIDENT_IDENTITY_PATH,
            root / DEFAULT_RESIDENT_IDENTITY_KEY_PATH,
        )
        evaluation = evaluate_runtime_receipts(
            receipts,
            repository_revision="a" * 40,
            plugin_version="remote-1",
            pre_restart_ready=True,
            current_resident=resident,
            evaluated_at=current[0],
        )
        serialized = receipts.model_dump_json().casefold()
        assert snapshot.phase == "ready_to_emit"
        assert snapshot.receipt_emitted is True
        assert snapshot.receipt_error is None
        assert evaluation.ready is True
        for forbidden in (
            '"text":',
            '"keys":',
            '"data_base64":',
            '"session_id":',
            '"subject":',
            '"cookie":',
            '"password":',
            '"otp":',
        ):
            assert forbidden not in serialized


def test_failed_write_stays_ready_and_duplicate_result_retries_publication() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        attempts: list[RuntimeReceiptSet] = []

        def flaky_publish(receipts: RuntimeReceiptSet, path: Path) -> Path:
            attempts.append(receipts)
            if len(attempts) == 1:
                raise OSError("simulated publication failure")
            return publish_runtime_receipts(receipts, path)

        runtime = _runtime(root, current, receipt_publisher=flaky_publish)
        cycle_binding = _advance_to_terminal(runtime, root, current)
        _observe(runtime, current, cycle_binding, _TERMINAL_SEQUENCE[:-1])
        final = _evidence(current, _TERMINAL_SEQUENCE[-1])

        first = runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=final,
        )
        after_failure = runtime.snapshot()
        path = root / _RECEIPT_RELATIVE_PATH
        path_exists_after_failure = path.exists()
        retry = runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=final,
        )

        assert first is RuntimeIngressStatus.ACCEPTED
        assert after_failure.phase == "ready_to_emit"
        assert after_failure.receipt_emitted is False
        assert after_failure.receipt_error == "OSError"
        assert path_exists_after_failure is False
        assert retry is RuntimeIngressStatus.ACCEPTED
        assert runtime.snapshot().receipt_emitted is True
        assert path.exists()
        assert len(attempts) == 2


def test_concurrent_duplicate_finalize_publishes_once() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        publications: list[RuntimeReceiptSet] = []

        def record_publish(receipts: RuntimeReceiptSet, path: Path) -> Path:
            publications.append(receipts)
            return publish_runtime_receipts(receipts, path)

        runtime = _runtime(root, current, receipt_publisher=record_publish)
        cycle_binding = _advance_to_terminal(runtime, root, current)
        _observe(runtime, current, cycle_binding, _TERMINAL_SEQUENCE[:-1])
        final = _evidence(current, _TERMINAL_SEQUENCE[-1])

        def finalize() -> RuntimeIngressStatus:
            return runtime.observe_terminal(
                thread_id="session-a",
                computer_session_id="computer-a",
                session_binding_sha256=_SESSION_BINDING,
                cycle_binding_sha256=cycle_binding,
                evidence=final,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = tuple(executor.map(lambda _index: finalize(), range(2)))

        assert RuntimeIngressStatus.ACCEPTED in statuses
        assert runtime.snapshot().receipt_emitted is True
        assert len(publications) == 1


def test_new_cycle_clears_previous_receipts_before_collecting_evidence() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        current = [_NOW]
        runtime = _runtime(root, current)
        cycle_binding = _advance_to_terminal(runtime, root, current)
        _observe(runtime, current, cycle_binding, _TERMINAL_SEQUENCE)
        path = root / _RECEIPT_RELATIVE_PATH
        identity_path = root / DEFAULT_RESIDENT_IDENTITY_PATH
        assert path.exists()
        assert identity_path.exists()
        current[0] += timedelta(seconds=1)

        result = runtime.start_cycle(_status(), root, "session-a")

        assert result == "started"
        assert path.exists() is False
        assert identity_path.exists() is False
        assert runtime.snapshot().phase == "waiting_browser"
        assert runtime.snapshot().receipt_emitted is False


def test_cycle_start_fails_closed_when_old_receipts_cannot_be_cleared() -> None:
    def fail_remove(_path: Path) -> None:
        raise OSError("simulated removal failure")

    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        runtime = _runtime(root, [_NOW], receipt_remover=fail_remove)

        with pytest.raises(RuntimeObservationStartError, match="invalidated"):
            _ = runtime.start_cycle(_status(), root, "session-a")

        assert runtime.snapshot().phase == "empty"


def _observe(
    runtime: RuntimeObservationRuntime,
    current: list[datetime],
    cycle_binding: str,
    sequence: tuple[tuple[ObservedTerminalTool, str, str | None], ...],
) -> None:
    for value in sequence:
        evidence = _evidence(current, value)
        assert runtime.observe_terminal(
            thread_id="session-a",
            computer_session_id="computer-a",
            session_binding_sha256=_SESSION_BINDING,
            cycle_binding_sha256=cycle_binding,
            evidence=evidence,
        ) == "accepted"


def _evidence(
    current: list[datetime],
    value: tuple[ObservedTerminalTool, str, str | None],
) -> TerminalIngressEvidence:
    current[0] += timedelta(seconds=1)
    tool_name, observation_id, expected_observation_id = value
    return TerminalIngressEvidence(
        tool_name=tool_name,
        observation_id=observation_id,
        expected_observation_id=expected_observation_id,
        identity_digest="d" * 64,
        recorded_at=current[0],
    )
