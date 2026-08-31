from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
import tempfile
from dataclasses import dataclass
from typing import cast
import unittest
from unittest import mock

from codex_app_server_transport_lifecycle import (
    AppServerGenerationMismatch,
    AppServerLifecycleSnapshot,
)
from codex_app_server_transport_attempt_context import get_turn_start_attempt_callbacks
from codex_app_server_transport_turn_outcomes import (
    TurnCompletion,
    TurnCompletionPending,
    TurnStatus,
)
from codex_discord_durable_queue_runtime import (
    DurableQueueRuntime,
    DurableQueueRuntimeDeps,
)
from codex_discord_durable_queue_restore import QueueRestoreUnstableError
import codex_discord_prompt_delivery_prepare as prompt_delivery_prepare
import codex_discord_prompt_mapped_delivery as prompt_mapped_delivery
from codex_discord_queue_job_memory import to_memory_queue_job
from codex_discord_runner_queue import QueueJobValue
from codex_discord_queue_processor import QueueGenerationExpiredError
from codex_discord_queue_processor import QueueAttemptNeedsReviewError
import codex_discord_store as store
from codex_discord_store_attempts import QueueAttemptState
from codex_discord_store_queue import QueueEnqueueResult


@dataclass(frozen=True, slots=True)
class FakeQueueChannel:
    id: int

    async def send(self, _text: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class FakeSourceMessage:
    id: int
    created_at: float


class FakeRestoreBot:
    def __init__(self) -> None:
        self.channel = FakeQueueChannel(id=222)
        self.cache_reads = 0

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]:
        _ = channel_id
        self.cache_reads += 1
        return cast(QueueJobValue, self.channel), "cache"

    async def fetch_channel(self, channel_id: int) -> QueueJobValue:
        _ = channel_id
        return cast(QueueJobValue, self.channel)

    def is_allowed_message_channel(self, channel: QueueJobValue) -> bool:
        _ = channel
        return True


class DurableQueueRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_late_success_wins_before_timeout_resolution(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-late")
            late_reconciliations: list[store.LateQueueAttemptReconciliation] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                late_success_before_error=True,
                late_reconciliations=late_reconciliations,
            )
            job = to_memory_queue_job(
                record,
                cast(QueueJobValue, FakeQueueChannel(id=222)),
                None,
            )

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            persisted = store.get_latest_queue_execution_attempt(db_path, record.job_id)

        self.assertEqual(attempt.turn_id, "turn-late")
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.state.value, "running")
        self.assertEqual(persisted.turn_id, "turn-late")
        self.assertEqual(
            [(item.job_id, item.target_thread_id, item.channel_id) for item in late_reconciliations],
            [(record.job_id, "thread-1", 222)],
        )

    async def test_exact_late_success_after_review_publishes_runner_wakeup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-late-after-review")
            late_reconciliations: list[store.LateQueueAttemptReconciliation] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                late_success_after_error=True,
                late_reconciliations=late_reconciliations,
            )
            job = to_memory_queue_job(
                record,
                cast(QueueJobValue, FakeQueueChannel(id=222)),
                None,
            )

            with self.assertRaises(QueueAttemptNeedsReviewError):
                _ = await runtime.acquire_turn(
                    job,
                    record.prompt,
                    record.target_thread_id,
                    recovery=False,
                )
            held = store.get_latest_queue_execution_attempt(db_path, record.job_id)
            self.assertIsNotNone(held)
            assert held is not None
            self.assertIs(held.state, QueueAttemptState.NEEDS_REVIEW)

            await asyncio.sleep(0.2)
            repaired = store.get_latest_queue_execution_attempt(db_path, record.job_id)

        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertIs(repaired.state, QueueAttemptState.RUNNING)
        self.assertEqual(repaired.turn_id, "turn-late")
        self.assertEqual(
            [(item.job_id, item.target_thread_id, item.channel_id) for item in late_reconciliations],
            [(record.job_id, "thread-1", 222)],
        )

    async def test_intake_claim_is_owned_before_generation_dependent_promotion(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            snapshots = iter(
                (
                    AppServerLifecycleSnapshot(3, True, 1.0),
                    AppServerLifecycleSnapshot(4, True, 2.0),
                )
            )
            latest = [AppServerLifecycleSnapshot(4, True, 2.0)]

            def lifecycle() -> AppServerLifecycleSnapshot:
                try:
                    value = next(snapshots)
                except StopIteration:
                    return latest[0]
                latest[0] = value
                return value

            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lifecycle,
            )

            intake = await runtime.intake_deferred(
                cast(QueueJobValue, FakeQueueChannel(id=222)),
                "survive post-commit generation change",
                "thread-1",
                source_message=cast(
                    QueueJobValue,
                    FakeSourceMessage(id=999, created_at=2.0),
                ),
            )

        self.assertIsNotNone(intake)
        assert intake is not None
        self.assertEqual(intake.jobs, ())
        self.assertEqual(intake.inbox.state.value, "received")
        self.assertTrue(intake.pending)

    async def test_generation_recovery_requeues_only_jobs_safe_to_replay(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            safe = self._enqueue(db_path, "job-safe")
            ambiguous = self._enqueue(
                db_path,
                "job-ambiguous",
                discord_message_id=1000,
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                ambiguous.job_id,
                baseline_turn_ids=(),
                app_server_generation=3,
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(4, True, 2.0),
            )
            channel = cast(QueueJobValue, FakeQueueChannel(id=222))

            recovery = await runtime.recover_generation_expired_jobs(
                (
                    to_memory_queue_job(safe, channel, None),
                    to_memory_queue_job(ambiguous, channel, None),
                )
            )
            records = store.list_queue_jobs(db_path)

        self.assertEqual([job.get("job_id") for job in recovery.jobs], ["job-safe"])
        self.assertEqual(recovery.jobs[0].get("app_server_generation"), 4)
        self.assertEqual(recovery.needs_review_job_ids, ("job-ambiguous",))
        self.assertEqual(
            {record.job_id: record.app_server_generation for record in records},
            {"job-safe": 4, "job-ambiguous": 3},
        )

    async def test_bot_restart_promotes_received_inbox_before_runner_restore(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=999,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="survive restart",
                source="gateway",
                normalization_version=1,
            )
            runtime = self._runtime(db_path, states={}, sent_prompts=[])

            restoration = await runtime.restore_deferred_inbox(FakeRestoreBot())

            inbox = store.list_deferred_discord_messages(db_path)

        self.assertEqual([job.get("discord_message_id") for job in restoration.jobs], [999])
        self.assertEqual(len(restoration.seeds), 1)
        self.assertEqual(inbox[0].state.value, "promoted")

    async def test_restore_continues_after_an_inaccessible_first_group(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for message_id, target, channel_id in (
                (900, "thread-missing", 111),
                (901, "thread-valid", 222),
            ):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=message_id,
                    target_thread_id=target,
                    channel_id=channel_id,
                    owner_user_id=7,
                    prompt=target,
                    source="gateway",
                    normalization_version=1,
                )
            runtime = self._runtime(db_path, states={}, sent_prompts=[])

            class GroupedRestoreBot(FakeRestoreBot):
                def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]:
                    if channel_id == 111:
                        return None, "miss"
                    return super().get_cached_channel_or_thread(channel_id)

                async def fetch_channel(self, channel_id: int) -> QueueJobValue:
                    if channel_id == 111:
                        raise OSError("channel unavailable")
                    return await super().fetch_channel(channel_id)

            restoration = await runtime.restore_deferred_inbox(GroupedRestoreBot())
            inbox = {row.message_id: row for row in store.list_deferred_discord_messages(db_path)}

        self.assertEqual(
            [job.get("discord_message_id") for job in restoration.jobs],
            [901],
        )
        self.assertEqual([seed.target_thread_id for seed in restoration.seeds], ["thread-valid"])
        self.assertEqual(inbox[900].state.value, "received")
        self.assertEqual(inbox[901].state.value, "promoted")

    async def test_unhealthy_restore_returns_a_coordinator_seed_for_later_promotion(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=999,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="survive unhealthy startup",
                source="gateway",
                normalization_version=1,
            )
            healthy = [False]
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(
                    3,
                    healthy[0],
                    1.0 if healthy[0] else None,
                ),
            )

            restoration = await runtime.restore_deferred_inbox(FakeRestoreBot())
            healthy[0] = True
            promoted = await runtime.promote_deferred_target("thread-1", channel_id=222)

        self.assertEqual(restoration.jobs, ())
        self.assertEqual(len(restoration.seeds), 1)
        self.assertEqual([job.discord_message_id for job in promoted], [999])

    async def test_unhealthy_gateway_intake_survives_and_promotes_after_recovery(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            healthy = [False]
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(
                    3,
                    healthy[0],
                    1.0 if healthy[0] else None,
                ),
            )
            channel = cast(QueueJobValue, FakeQueueChannel(id=222))
            source = cast(
                QueueJobValue,
                FakeSourceMessage(id=999, created_at=2.0),
            )

            intake = await runtime.intake_deferred(
                channel,
                "keep this",
                "thread-1",
                source_message=source,
            )

            self.assertIsNotNone(intake)
            assert intake is not None
            self.assertTrue(intake.pending)
            self.assertEqual(intake.jobs, ())
            self.assertEqual(store.list_queue_jobs(db_path), [])
            self.assertTrue(store.is_processed_discord_message_id(db_path, 999))

            healthy[0] = True
            promoted = await runtime.promote_deferred_target(
                "thread-1",
                channel_id=222,
            )

            inbox = store.list_deferred_discord_messages(db_path)

        self.assertEqual([job.discord_message_id for job in promoted], [999])
        self.assertEqual(inbox[0].state.value, "promoted")

    async def test_retracting_last_queue_job_notifies_app_server_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = self._enqueue(db_path, "job-1")
            work_change_events: list[str] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                work_change_events=work_change_events,
            )

            record = await runtime.retract_job(
                "thread-1",
                channel_id=222,
                owner_user_id=7,
            )

            self.assertIsNotNone(record)
            self.assertEqual(work_change_events, ["changed"])
            self.assertEqual(store.list_queue_jobs(db_path), [])

    async def test_enqueue_holds_app_server_admission_through_store(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            admission_events: list[str] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                admission_events=admission_events,
            )
            original_enqueue = store.enqueue_queue_job

            def observed_enqueue(
                db_path_arg: Path,
                *,
                job_id: str,
                target_thread_id: str,
                channel_id: int,
                owner_user_id: int | None,
                discord_message_id: int | None,
                app_server_generation: int,
                prompt: str,
                queued: bool,
                ack_sent: bool,
                created_at: float | None = None,
            ) -> QueueEnqueueResult:
                admission_events.append("store")
                return original_enqueue(
                    db_path_arg,
                    job_id=job_id,
                    target_thread_id=target_thread_id,
                    channel_id=channel_id,
                    owner_user_id=owner_user_id,
                    discord_message_id=discord_message_id,
                    app_server_generation=app_server_generation,
                    prompt=prompt,
                    queued=queued,
                    ack_sent=ack_sent,
                    created_at=created_at,
                )

            with mock.patch.object(store, "enqueue_queue_job", observed_enqueue):
                _ = await runtime.enqueue(
                    cast(QueueJobValue, FakeQueueChannel(id=222)),
                    "request",
                    "thread-1",
                    queued=True,
                    ack_sent=True,
                    source_message=cast(
                        QueueJobValue,
                        FakeSourceMessage(id=999, created_at=2.0),
                    ),
                )

            self.assertEqual(admission_events, ["enter", "store", "exit"])

    async def test_legacy_starting_job_is_not_blindly_matched_to_a_new_turn(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            record = store.begin_queue_job_attempt(
                db_path,
                record.job_id,
                baseline_turn_ids=("turn-old",),
                app_server_generation=3,
            )
            sent_prompts: list[str] = []
            runtime = self._runtime(
                db_path,
                states={
                    "turn-old": self._turn("turn-old", TurnStatus.COMPLETED),
                    "turn-new": self._turn("turn-new", TurnStatus.IN_PROGRESS),
                },
                sent_prompts=sent_prompts,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            with self.assertRaises(QueueAttemptNeedsReviewError):
                _ = await runtime.acquire_turn(
                    job,
                    record.prompt,
                    record.target_thread_id,
                    recovery=False,
                )

            persisted = store.list_queue_jobs(db_path)[0]
            self.assertEqual(sent_prompts, [])
            self.assertIsNone(persisted.turn_id)

    async def test_exec_pending_job_can_retry_because_write_boundary_was_not_crossed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            pending = store.begin_queue_execution_attempt(
                db_path,
                record.job_id,
                baseline_turn_ids=("turn-old",),
                app_server_generation=3,
            )
            record = store.list_queue_jobs(db_path)[0]
            sent_prompts: list[str] = []
            runtime = self._runtime(
                db_path,
                states={"turn-old": self._turn("turn-old", TurnStatus.COMPLETED)},
                sent_prompts=sent_prompts,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            persisted = store.list_queue_jobs(db_path)[0]
            self.assertEqual(pending.state.value, "exec_pending")
            self.assertEqual(attempt.attempt_number, 2)
            self.assertEqual(sent_prompts, ["request"])
            self.assertEqual(persisted.attempt_count, 2)
            self.assertEqual(persisted.turn_id, "turn-sent")

    async def test_immediate_job_preserves_non_queued_start_ack(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1", queued=False, ack_sent=False)
            prompt_kwargs: list[dict[str, object]] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                prompt_kwargs=prompt_kwargs,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            _ = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            self.assertEqual(len(prompt_kwargs), 1)
            self.assertIs(prompt_kwargs[0]["queued"], False)
            self.assertIs(prompt_kwargs[0]["ack_sent"], False)

    async def test_pending_job_waits_for_existing_in_progress_turn_before_sending(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            sent_prompts: list[str] = []
            state_reads = 0

            def get_states(_thread_id: str) -> dict[str, TurnCompletion]:
                nonlocal state_reads
                state_reads += 1
                status = TurnStatus.IN_PROGRESS if state_reads == 1 else TurnStatus.COMPLETED
                return {"turn-existing": self._turn("turn-existing", status)}

            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=sent_prompts,
                get_states=get_states,
            )
            channel = FakeQueueChannel(id=222)
            job = to_memory_queue_job(record, cast(QueueJobValue, channel), None)

            attempt = await runtime.acquire_turn(
                job,
                record.prompt,
                record.target_thread_id,
                recovery=False,
            )

            self.assertEqual(state_reads, 2)
            self.assertEqual(sent_prompts, ["request"])
            self.assertEqual(attempt.turn_id, "turn-sent")

    async def test_enqueue_persists_current_app_server_generation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            runtime = self._runtime(db_path, states={}, sent_prompts=[])
            channel = FakeQueueChannel(id=222)

            job, created, position = await runtime.enqueue(
                cast(QueueJobValue, channel),
                "request",
                "thread-1",
                queued=True,
                ack_sent=True,
                source_message=None,
            )

            records = store.list_queue_jobs(db_path)

        self.assertTrue(created)
        self.assertEqual(position, 1)
        self.assertEqual(job.get("app_server_generation"), 3)
        self.assertEqual(records[0].app_server_generation, 3)

    async def test_enqueue_rejects_source_message_from_before_accepting_since(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            runtime = self._runtime(db_path, states={}, sent_prompts=[])
            channel = FakeQueueChannel(id=222)
            source = FakeSourceMessage(id=999, created_at=0.5)

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.enqueue(
                    cast(QueueJobValue, channel),
                    "request",
                    "thread-1",
                    queued=True,
                    ack_sent=True,
                    source_message=cast(QueueJobValue, source),
                )

            self.assertEqual(store.list_queue_jobs(db_path), [])

    async def test_enqueue_rejects_generation_changed_after_prompt_admission(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(4, True, 2.0),
                expected_generation=3,
            )
            channel = FakeQueueChannel(id=222)

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.enqueue(
                    cast(QueueJobValue, channel),
                    "request",
                    "thread-1",
                    queued=True,
                    ack_sent=True,
                    source_message=None,
                )

            self.assertEqual(store.list_queue_jobs(db_path), [])

    async def test_stale_cleanup_preserves_both_observed_and_new_generation_jobs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-old",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=998,
                app_server_generation=2,
                prompt="old",
                queued=True,
                ack_sent=True,
            )
            lifecycle_calls = 0

            def lifecycle() -> AppServerLifecycleSnapshot:
                nonlocal lifecycle_calls
                lifecycle_calls += 1
                if lifecycle_calls == 1:
                    return AppServerLifecycleSnapshot(3, True, 1.0)
                if lifecycle_calls == 2:
                    _ = store.enqueue_queue_job(
                        db_path,
                        job_id="job-new",
                        target_thread_id="thread-1",
                        channel_id=222,
                        owner_user_id=7,
                        discord_message_id=999,
                        app_server_generation=4,
                        prompt="new",
                        queued=True,
                        ack_sent=True,
                    )
                return AppServerLifecycleSnapshot(4, True, 2.0)

            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lifecycle,
                expected_generation=3,
            )

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.enqueue(
                    cast(QueueJobValue, FakeQueueChannel(id=222)),
                    "request",
                    "thread-1",
                    queued=True,
                    ack_sent=True,
                    source_message=None,
                )

            records = store.list_queue_jobs(db_path)

        self.assertEqual([record.job_id for record in records], ["job-old", "job-new"])
        self.assertEqual([record.app_server_generation for record in records], [4, 4])

    async def test_unhealthy_enqueue_preserves_existing_job_without_storing_new_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = self._enqueue(db_path, "job-old")
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(3, False, None),
            )
            channel = FakeQueueChannel(id=222)

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.enqueue(
                    cast(QueueJobValue, channel),
                    "new request",
                    "thread-1",
                    queued=True,
                    ack_sent=True,
                    source_message=None,
                )

            records = store.list_queue_jobs(db_path)

        self.assertEqual([record.job_id for record in records], ["job-old"])

    async def test_stale_unwritten_job_is_rebound_without_turn_delivery(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            record = self._enqueue(db_path, "job-1")
            sent_prompts: list[str] = []
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=sent_prompts,
                lifecycle=lambda: AppServerLifecycleSnapshot(4, True, 2.0),
            )
            job = to_memory_queue_job(
                record,
                cast(QueueJobValue, FakeQueueChannel(id=222)),
                None,
            )

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.acquire_turn(
                    job,
                    record.prompt,
                    record.target_thread_id,
                    recovery=False,
                )

            self.assertEqual(sent_prompts, [])
            records = store.list_queue_jobs(db_path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].app_server_generation, 4)

    async def test_generation_change_while_waiting_fences_memory_but_preserves_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = self._enqueue(db_path, "job-1")
            snapshot_reads = 0

            def lifecycle() -> AppServerLifecycleSnapshot:
                nonlocal snapshot_reads
                snapshot_reads += 1
                if snapshot_reads == 1:
                    return AppServerLifecycleSnapshot(3, True, 1.0)
                return AppServerLifecycleSnapshot(3, False, None)

            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lifecycle,
            )

            with self.assertRaises(QueueGenerationExpiredError):
                _ = await runtime.wait_for_turn_completion("thread-1", "turn-1", 3)

            records = store.list_queue_jobs(db_path)

        self.assertEqual([record.job_id for record in records], ["job-1"])

    async def test_restore_adopts_all_persisted_jobs_into_current_generation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-stale",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=998,
                app_server_generation=2,
                prompt="stale",
                queued=True,
                ack_sent=True,
            )
            _ = self._enqueue(db_path, "job-current")
            runtime = self._runtime(db_path, states={}, sent_prompts=[])
            bot = FakeRestoreBot()

            jobs = await runtime.restore_jobs(bot)
            records = store.list_queue_jobs(db_path)

        self.assertCountEqual(
            [job.get("job_id") for job in jobs],
            ["job-stale", "job-current"],
        )
        self.assertCountEqual(
            [record.job_id for record in records],
            ["job-stale", "job-current"],
        )
        self.assertTrue(all(record.app_server_generation == 3 for record in records))
        self.assertEqual(bot.cache_reads, 2)

    async def test_restore_while_unhealthy_surfaces_failure_without_resolving_channels(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = self._enqueue(db_path, "job-1")
            runtime = self._runtime(
                db_path,
                states={},
                sent_prompts=[],
                lifecycle=lambda: AppServerLifecycleSnapshot(3, False, None),
            )
            bot = FakeRestoreBot()

            with self.assertRaises(QueueRestoreUnstableError):
                _ = await runtime.restore_jobs(bot)

            self.assertEqual(
                [record.job_id for record in store.list_queue_jobs(db_path)],
                ["job-1"],
            )
            self.assertEqual(bot.cache_reads, 0)

    def _runtime(
        self,
        db_path: Path,
        *,
        states: dict[str, TurnCompletion],
        sent_prompts: list[str],
        get_states: Callable[[str], dict[str, TurnCompletion]] | None = None,
        lifecycle: Callable[[], AppServerLifecycleSnapshot] | None = None,
        expected_generation: int | None = None,
        prompt_error: AppServerGenerationMismatch | None = None,
        late_success_before_error: bool = False,
        late_success_after_error: bool = False,
        prompt_kwargs: list[dict[str, object]] | None = None,
        admission_events: list[str] | None = None,
        work_change_events: list[str] | None = None,
        late_reconciliations: list[store.LateQueueAttemptReconciliation] | None = None,
    ) -> DurableQueueRuntime:
        async def run_prompt(
            channel: QueueJobValue,
            prompt: str,
            **kwargs: QueueJobValue,
        ) -> prompt_delivery_prepare.PromptDeliveryPreparationResult:
            _ = channel
            if prompt_error is not None:
                raise prompt_error
            sent_prompts.append(prompt)
            if prompt_kwargs is not None:
                prompt_kwargs.append(dict(kwargs))
            callbacks = get_turn_start_attempt_callbacks()
            self.assertIsNotNone(callbacks)
            assert callbacks is not None
            generation = cast(int, cast(object, kwargs["expected_app_server_generation"]))
            request_id = f"test-request-{len(sent_prompts)}"
            callbacks.before_write(request_id, 4242, generation)
            callbacks.after_write(request_id, 4242, generation)
            if late_success_before_error:
                callbacks.late_success(
                    request_id,
                    4242,
                    generation,
                    "thread-1",
                    "turn-late",
                )
                raise TimeoutError("turn/start response timed out")
            if late_success_after_error:
                _ = asyncio.get_running_loop().call_later(
                    0.1,
                    callbacks.late_success,
                    request_id,
                    4242,
                    generation,
                    "thread-1",
                    "turn-late",
                )
                raise TimeoutError("turn/start response timed out before late success")
            return prompt_delivery_prepare.PromptDeliveryPreparationResult(
                handled=True,
                target_thread_id="thread-1",
                target_ref="thread-1",
                recent_offsets={},
                delegate_to_session_mirror=False,
                mapped_result=prompt_mapped_delivery.MappedPromptDeliveryResult(
                    handled=True,
                    accepted=True,
                    turn_id="turn-sent",
                ),
            )

        async def send_chunks(
            target: QueueJobValue,
            text: str,
            **_kwargs: QueueJobValue,
        ) -> int:
            _ = target, text
            return 1

        lifecycle_getter = lifecycle or (lambda: AppServerLifecycleSnapshot(3, True, 1.0))

        @contextmanager
        def admit(_expected: int | None):
            if admission_events is not None:
                admission_events.append("enter")
            try:
                yield lifecycle_getter()
            finally:
                if admission_events is not None:
                    admission_events.append("exit")

        return DurableQueueRuntime(
            DurableQueueRuntimeDeps(
                get_db_path=lambda: db_path,
                get_app_server_lifecycle=lifecycle_getter,
                ensure_app_server_ready=lambda: None,
                get_expected_app_server_generation=lambda: expected_generation,
                admit_app_server_generation=admit,
                notify_app_server_work_changed=(
                    (lambda: work_change_events.append("changed"))
                    if work_change_events is not None
                    else (lambda: None)
                ),
                notify_late_queue_attempt_reconciled=(
                    lambda reconciliation, _job, _loop: (
                        late_reconciliations.append(reconciliation)
                        if late_reconciliations is not None
                        else None
                    )
                ),
                get_turn_states=(
                    (lambda thread_id, _generation: get_states(thread_id))
                    if get_states is not None
                    else (lambda _thread_id, _generation: states)
                ),
                wait_for_live_turn=lambda _thread_id, _turn_id, _timeout, _generation: TurnCompletionPending(),
                run_prompt_and_send=run_prompt,
                send_chunks=send_chunks,
                log=lambda _message: None,
            )
        )

    @staticmethod
    def _enqueue(
        db_path: Path,
        job_id: str,
        *,
        discord_message_id: int = 999,
        queued: bool = True,
        ack_sent: bool = True,
    ):
        return store.enqueue_queue_job(
            db_path,
            job_id=job_id,
            target_thread_id="thread-1",
            channel_id=222,
            owner_user_id=7,
            discord_message_id=discord_message_id,
            app_server_generation=3,
            prompt="request",
            queued=queued,
            ack_sent=ack_sent,
            created_at=1.0,
        ).job

    @staticmethod
    def _turn(turn_id: str, status: TurnStatus) -> TurnCompletion:
        return TurnCompletion("thread-1", turn_id, status)


if __name__ == "__main__":
    _ = unittest.main()
