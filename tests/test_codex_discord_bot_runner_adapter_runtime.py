from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast
import unittest
from unittest import mock

import codex_discord_bot_runner_adapter_runtime as adapter_runtime
from codex_discord_runner_queue import QueueJob


class FakeClient:
    def __init__(self) -> None:
        self.external_work_guard: Callable[[], bool] | None = None

    def set_external_work_guard(self, guard: Callable[[], bool] | None) -> None:
        self.external_work_guard = guard

    def lifecycle_snapshot(self) -> object:
        return object()

    def start(self) -> None:
        return None

    def delivery_admission(self, _generation: int | None) -> object:
        return object()

    def notify_child_cleanup_blocker_changed(self) -> None:
        return None

    def get_thread_turn_states(self, *_args: object, **_kwargs: object) -> object:
        return object()

    def wait_for_turn_completion(self, *_args: object, **_kwargs: object) -> object:
        return object()


class BotRunnerAdapterRuntimeTests(unittest.TestCase):
    def test_durable_queue_registers_external_queue_guard_on_shared_client(self) -> None:
        module = ModuleType("fake_bot_runner_adapter")
        db_path = Path("mirror.sqlite")
        setattr(module, "MIRROR_DB_PATH", db_path)
        setattr(module, "log_line", lambda _text: None)
        client = FakeClient()

        with mock.patch.object(
            adapter_runtime.app_server_transport,
            "DEFAULT_CLIENT",
            client,
        ):
            _ = adapter_runtime.BotRunnerAdapterRuntime(
                module=module
            ).make_durable_queue_runtime()

        guard = client.external_work_guard
        self.assertIsNotNone(guard)
        assert guard is not None
        with mock.patch.object(
            adapter_runtime.store,
            "has_executable_queue_work",
            return_value=True,
        ) as has_work:
            self.assertTrue(guard())
        has_work.assert_called_once_with(db_path)


class BotRunnerAdapterLateWakeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_thread_late_success_wakes_discord_runner_on_owning_loop(self) -> None:
        module = ModuleType("fake_bot_runner_adapter_late_wake")
        wake_event = asyncio.Event()
        calls: list[tuple[str, str]] = []

        class _Runner:
            def wake_reconciled_queue_job(
                self,
                reconciliation: adapter_runtime.store.LateQueueAttemptReconciliation,
                job: QueueJob,
            ) -> None:
                calls.append((reconciliation.job_id, str(job.get("job_id"))))
                wake_event.set()

        setattr(module, "RUNNER_RUNTIME", _Runner())
        runtime = adapter_runtime.BotRunnerAdapterRuntime(module=module)
        reconciliation = adapter_runtime.store.LateQueueAttemptReconciliation(
            job_id="job-late",
            target_thread_id="thread-1",
            channel_id=222,
        )
        job = cast(QueueJob, cast(object, {"job_id": "job-late"}))

        await asyncio.to_thread(
            runtime.notify_late_queue_attempt_reconciled,
            reconciliation,
            job,
            asyncio.get_running_loop(),
        )
        await asyncio.wait_for(wake_event.wait(), timeout=1.0)

        self.assertEqual(calls, [("job-late", "job-late")])


if __name__ == "__main__":
    _ = unittest.main()
