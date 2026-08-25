from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import cast
import tempfile
import unittest
from unittest import mock

from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_discord_durable_queue_runtime import DeferredIntakeResult
from codex_discord_runner_queue import QueueJob, QueueJobValue
from codex_discord_runner_runtime import RunnerRuntime, RunnerRuntimeDeps
from codex_discord_store_attempts import QueueAttemptState
import codex_discord_store as store


@dataclass(frozen=True, slots=True)
class _Author:
    id: int = 7


@dataclass(frozen=True, slots=True)
class _Message:
    id: int = 2001
    author: _Author = _Author()


@dataclass(frozen=True, slots=True)
class _Channel:
    id: int = 222

    async def send(self, _text: str) -> None:
        return None


class _PostClaimDurableQueue:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.promotion_attempts = 0
        self.deps = SimpleNamespace(
            get_db_path=lambda: db_path,
            get_app_server_lifecycle=lambda: AppServerLifecycleSnapshot(3, True, 1.0),
        )

    async def intake_deferred(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str,
        *,
        source_message: QueueJobValue,
    ) -> DeferredIntakeResult:
        claim = store.claim_deferred_discord_message(
            self.db_path,
            message_id=int(getattr(source_message, "id")),
            target_thread_id=target_thread_id,
            channel_id=int(getattr(channel, "id")),
            owner_user_id=int(getattr(getattr(source_message, "author"), "id")),
            prompt=prompt,
            source="gateway",
            normalization_version=1,
        )
        return DeferredIntakeResult(claim.record, (), True)

    async def promote_deferred_target(
        self,
        target_thread_id: str,
        *,
        channel_id: int,
    ):
        self.promotion_attempts += 1
        if self.promotion_attempts == 1:
            raise RuntimeError("injected post-claim promotion failure")
        return store.promote_deferred_discord_messages(
            self.db_path,
            target_thread_id=target_thread_id,
            channel_id=channel_id,
            app_server_generation=3,
            lease_owner="ownership-test",
        ).jobs

    async def reconcile_deferred_target(
        self,
        _target_thread_id: str,
        *,
        channel_id: int,
    ) -> tuple[store.StoredQueueJob, ...]:
        _ = channel_id
        return ()


class _WakeDurableQueue(_PostClaimDurableQueue):
    async def promote_deferred_target(
        self,
        target_thread_id: str,
        *,
        channel_id: int,
    ):
        self.promotion_attempts += 1
        return store.promote_deferred_discord_messages(
            self.db_path,
            target_thread_id=target_thread_id,
            channel_id=channel_id,
            app_server_generation=3,
            lease_owner="wake-test",
        ).jobs

    async def reconcile_deferred_target(
        self,
        target_thread_id: str,
        *,
        channel_id: int,
    ):
        return tuple(
            store.list_executable_queue_jobs(
                self.db_path,
                target_thread_id=target_thread_id,
                channel_id=channel_id,
                app_server_generation=3,
            )
        )


class RunnerRuntimeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_claim_promotion_failure_keeps_a_recovery_owner(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            durable = _PostClaimDurableQueue(db_path)
            promoted = asyncio.Event()
            logs: list[str] = []

            async def send_chunks(
                target: QueueJobValue,
                text: str,
                **kwargs: QueueJobValue,
            ) -> int:
                _ = target, text, kwargs
                return 1

            deps = cast(
                RunnerRuntimeDeps,
                cast(
                    object,
                    SimpleNamespace(
                        durable_queue=durable,
                        send_chunks=send_chunks,
                        log=logs.append,
                    ),
                ),
            )
            runtime = RunnerRuntime(deps)

            async def record_promoted(
                _runtime: RunnerRuntime,
                records: tuple[object, ...],
                *,
                channel: QueueJobValue,
                source_message: QueueJobValue,
            ) -> None:
                _ = channel, source_message
                if records:
                    promoted.set()

            with mock.patch.object(
                RunnerRuntime,
                "_enqueue_promoted_jobs",
                new=record_promoted,
            ):
                owned = await runtime._defer_plain_ask_owned(
                    cast(QueueJobValue, _Channel()),
                    "preserve me",
                    "thread-1",
                    source_message=cast(QueueJobValue, _Message()),
                )
                await asyncio.wait_for(promoted.wait(), timeout=2.0)

                inbox = store.list_deferred_discord_messages(db_path)
                self.assertTrue(owned)
                self.assertTrue(store.is_processed_discord_message_id(db_path, 2001))
                self.assertEqual([(row.message_id, row.state.value) for row in inbox], [(2001, "promoted")])
                self.assertGreaterEqual(durable.promotion_attempts, 2)
                self.assertTrue(any(not task.done() for task in runtime._deferred_tasks.values()))
                self.assertTrue(any("deferred_replay_task_failed" in line for line in logs))

                tasks = tuple(runtime._deferred_tasks.values())
                for task in tasks:
                    _ = task.cancel()
                _ = await asyncio.gather(*tasks, return_exceptions=True)

    async def test_late_reconciliation_starts_target_channel_coordinator(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            durable = _PostClaimDurableQueue(Path(temp_dir) / "mirror.sqlite")
            started = asyncio.Event()
            logs: list[str] = []
            runtime = RunnerRuntime(
                cast(
                    RunnerRuntimeDeps,
                    cast(
                        object,
                        SimpleNamespace(
                            durable_queue=durable,
                            log=logs.append,
                        ),
                    ),
                )
            )

            async def record_loop(
                _runtime: RunnerRuntime,
                target_thread_id: str,
                *,
                channel: QueueJobValue,
            ) -> None:
                self.assertEqual(target_thread_id, "thread-1")
                self.assertEqual(int(getattr(channel, "id")), 222)
                started.set()

            reconciliation = store.LateQueueAttemptReconciliation(
                job_id="job-late",
                target_thread_id="thread-1",
                channel_id=222,
            )
            job = cast(
                QueueJob,
                cast(object, {"job_id": "job-late", "channel": _Channel()}),
            )
            with mock.patch.object(
                RunnerRuntime,
                "_deferred_replay_loop",
                new=record_loop,
            ):
                runtime.wake_reconciled_queue_job(
                    reconciliation,
                    job,
                )
                await asyncio.wait_for(started.wait(), timeout=1.0)

            self.assertTrue(any("queue_turn_late_start_wakeup_started" in line for line in logs))

    async def test_inbox_claim_wake_during_final_empty_query_is_not_lost(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            durable = _WakeDurableQueue(db_path)
            query_read = threading.Event()
            release_query = threading.Event()
            enqueued = asyncio.Event()
            runtime = RunnerRuntime(
                cast(
                    RunnerRuntimeDeps,
                    cast(
                        object,
                        SimpleNamespace(
                            durable_queue=durable,
                            log=lambda _message: None,
                        ),
                    ),
                )
            )
            original_has_executable = store.has_executable_queue_jobs_for_target_channel
            blocked = False

            def block_first_empty_query(
                db_path_arg: Path,
                *,
                target_thread_id: str,
                channel_id: int,
                app_server_generation: int | None = None,
            ) -> bool:
                nonlocal blocked
                result = original_has_executable(
                    db_path_arg,
                    target_thread_id=target_thread_id,
                    channel_id=channel_id,
                    app_server_generation=app_server_generation,
                )
                if not result and not blocked:
                    blocked = True
                    query_read.set()
                    if not release_query.wait(timeout=5.0):
                        raise TimeoutError("wake-query barrier was not released")
                return result

            async def record_enqueued(
                _runtime: RunnerRuntime,
                records: tuple[object, ...],
                *,
                channel: QueueJobValue,
                source_message: QueueJobValue,
            ) -> None:
                _ = channel, source_message
                for record in records:
                    stored_record = cast(store.StoredQueueJob, record)
                    if stored_record.discord_message_id != 2002:
                        continue
                    self.assertTrue(store.complete_queue_job(db_path, stored_record.job_id))
                    enqueued.set()

            channel = cast(QueueJobValue, _Channel())
            try:
                with (
                    mock.patch.object(
                        store,
                        "has_executable_queue_jobs_for_target_channel",
                        side_effect=block_first_empty_query,
                    ),
                    mock.patch.object(
                        RunnerRuntime,
                        "_enqueue_promoted_jobs",
                        new=record_enqueued,
                    ),
                ):
                    runtime._ensure_deferred_replay_task("thread-1", channel=channel)
                    self.assertTrue(await asyncio.to_thread(query_read.wait, 2.0))
                    _ = store.claim_deferred_discord_message(
                        db_path,
                        message_id=2002,
                        target_thread_id="thread-1",
                        channel_id=222,
                        owner_user_id=7,
                        prompt="arrived during final query",
                        source="gateway",
                        normalization_version=1,
                    )
                    runtime._ensure_deferred_replay_task("thread-1", channel=channel)
                    release_query.set()
                    await asyncio.wait_for(enqueued.wait(), timeout=2.0)
                    self.assertEqual(store.list_queue_jobs(db_path), [])
                    self.assertIs(
                        store.list_deferred_discord_messages(db_path)[0].state,
                        store.DeferredInboxState.COMPLETED,
                    )
            finally:
                release_query.set()
                await self._cancel_deferred_tasks(runtime)

            self.assertGreaterEqual(
                runtime._deferred_wake_generations[("thread-1", 222)],
                2,
            )

    async def test_late_reconciliation_wake_during_final_empty_query_is_not_lost(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            durable = _WakeDurableQueue(db_path)
            claim = store.claim_deferred_discord_message(
                db_path,
                message_id=2003,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="late accepted turn",
                source="gateway",
                normalization_version=1,
            )
            promotion = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=3,
                lease_owner="late-wake-test",
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                promotion.jobs[0].job_id,
                app_server_generation=3,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-late",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            _ = store.mark_queue_attempt_needs_review(
                db_path,
                attempt.attempt_id,
                last_error="timeout",
            )
            query_read = threading.Event()
            release_query = threading.Event()
            enqueued = asyncio.Event()
            runtime = RunnerRuntime(
                cast(
                    RunnerRuntimeDeps,
                    cast(
                        object,
                        SimpleNamespace(
                            durable_queue=durable,
                            log=lambda _message: None,
                        ),
                    ),
                )
            )
            original_has_executable = store.has_executable_queue_jobs_for_target_channel
            blocked = False

            def block_first_empty_query(
                db_path_arg: Path,
                *,
                target_thread_id: str,
                channel_id: int,
                app_server_generation: int | None = None,
            ) -> bool:
                nonlocal blocked
                result = original_has_executable(
                    db_path_arg,
                    target_thread_id=target_thread_id,
                    channel_id=channel_id,
                    app_server_generation=app_server_generation,
                )
                if not result and not blocked:
                    blocked = True
                    query_read.set()
                    if not release_query.wait(timeout=5.0):
                        raise TimeoutError("late-wake query barrier was not released")
                return result

            async def record_enqueued(
                _runtime: RunnerRuntime,
                records: tuple[object, ...],
                *,
                channel: QueueJobValue,
                source_message: QueueJobValue,
            ) -> None:
                _ = channel, source_message
                for record in records:
                    stored_record = cast(store.StoredQueueJob, record)
                    if stored_record.job_id != promotion.jobs[0].job_id:
                        continue
                    store.complete_queue_execution_attempt(
                        db_path,
                        job_id=stored_record.job_id,
                        attempt_id=attempt.attempt_id,
                    )
                    enqueued.set()

            channel = cast(QueueJobValue, _Channel())
            try:
                with (
                    mock.patch.object(
                        store,
                        "has_executable_queue_jobs_for_target_channel",
                        side_effect=block_first_empty_query,
                    ),
                    mock.patch.object(
                        RunnerRuntime,
                        "_enqueue_promoted_jobs",
                        new=record_enqueued,
                    ),
                ):
                    runtime._ensure_deferred_replay_task("thread-1", channel=channel)
                    self.assertTrue(await asyncio.to_thread(query_read.wait, 2.0))
                    reconciliation = store.reconcile_late_queue_attempt_running(
                        db_path,
                        client_request_id="request-late",
                        app_server_process_id=4242,
                        app_server_generation=3,
                        target_thread_id="thread-1",
                        turn_id="turn-late",
                    )
                    self.assertIsNotNone(reconciliation)
                    assert reconciliation is not None
                    runtime.wake_reconciled_queue_job(
                        reconciliation,
                        cast(
                            QueueJob,
                            cast(
                                object,
                                {
                                    "job_id": promotion.jobs[0].job_id,
                                    "channel": _Channel(),
                                },
                            ),
                        ),
                    )
                    release_query.set()
                    await asyncio.wait_for(enqueued.wait(), timeout=2.0)
                    self.assertEqual(store.list_queue_jobs(db_path), [])
                    self.assertIs(
                        store.list_deferred_discord_messages(db_path)[0].state,
                        store.DeferredInboxState.COMPLETED,
                    )
                    persisted_attempt = store.get_latest_queue_execution_attempt(
                        db_path,
                        promotion.jobs[0].job_id,
                    )
                    self.assertIsNotNone(persisted_attempt)
                    assert persisted_attempt is not None
                    self.assertIs(
                        persisted_attempt.state,
                        QueueAttemptState.TURN_TERMINAL,
                    )
                    self.assertEqual(
                        len(store.list_queue_execution_attempts(db_path)),
                        1,
                    )
            finally:
                release_query.set()
                await self._cancel_deferred_tasks(runtime)

            self.assertIs(claim.record.state, store.DeferredInboxState.RECEIVED)
            self.assertGreaterEqual(
                runtime._deferred_wake_generations[("thread-1", 222)],
                2,
            )

    @staticmethod
    async def _cancel_deferred_tasks(runtime: RunnerRuntime) -> None:
        tasks = tuple(runtime._deferred_tasks.values())
        for task in tasks:
            _ = task.cancel()
        if tasks:
            _ = await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    _ = unittest.main()
