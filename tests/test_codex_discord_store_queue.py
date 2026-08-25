from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
import sqlite3
from threading import Event

import codex_discord_store as store
from codex_discord_store_inbox import DeferredInboxState
from codex_discord_store_queue import QueueJobState, StoredQueueJob


class QueueStoreTests(unittest.TestCase):
    def test_review_held_job_does_not_count_as_executable_external_work(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="review me",
                source="gateway",
                normalization_version=1,
            )
            promotion = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="test",
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                promotion.jobs[0].job_id,
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            before_review = store.list_executable_queue_jobs(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
            )
            _ = store.mark_queue_attempt_needs_review(
                db_path,
                attempt.attempt_id,
                last_error="operator review required",
            )
            inbox = store.list_deferred_discord_messages(db_path)
            after_review = store.list_executable_queue_jobs(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
            )

            executable = store.has_executable_queue_work(db_path)
            target_executable = store.has_executable_queue_jobs_for_target_channel(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
            )

        self.assertFalse(executable)
        self.assertFalse(target_executable)
        self.assertEqual([job.job_id for job in before_review], [promotion.jobs[0].job_id])
        self.assertEqual(after_review, [])
        self.assertIs(inbox[0].state, DeferredInboxState.NEEDS_REVIEW)

    def test_flush_cancels_promoted_inbox_rows_in_the_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for message_id in (901, 902):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=message_id,
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    prompt=f"request {message_id}",
                    source="gateway",
                    normalization_version=1,
                )
            promoted = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="test",
            )

            deleted = store.flush_queue_jobs(
                db_path,
                "thread-1",
                app_server_generation=4,
            )
            inbox = store.list_deferred_discord_messages(db_path)

        self.assertEqual(len(promoted.jobs), 2)
        self.assertEqual(len(deleted), 2)
        self.assertTrue(all(row.state is DeferredInboxState.FAILED for row in inbox))

    def test_retract_cancels_its_promoted_inbox_row(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="retract me",
                source="gateway",
                normalization_version=1,
            )
            promoted = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="test",
            )

            retracted = store.retract_queue_job(
                db_path,
                "thread-1",
                channel_id=222,
                owner_user_id=7,
            )
            inbox = store.list_deferred_discord_messages(db_path)

        self.assertEqual(len(promoted.jobs), 1)
        self.assertIsNotNone(retracted)
        self.assertIs(inbox[0].state, DeferredInboxState.CANCELLED)

    def test_queue_job_survives_reopen_and_duplicate_discord_message_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            first = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=999,
                app_server_generation=4,
                prompt="first request",
                queued=True,
                ack_sent=True,
                created_at=10.0,
            )
            duplicate = store.enqueue_queue_job(
                db_path,
                job_id="job-duplicate",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=999,
                app_server_generation=4,
                prompt="first request",
                queued=True,
                ack_sent=True,
                created_at=11.0,
            )

            records = store.list_queue_jobs(db_path)

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.job.job_id, "job-1")
        self.assertEqual([record.job_id for record in records], ["job-1"])
        self.assertIs(records[0].state, QueueJobState.PENDING)
        self.assertEqual(records[0].app_server_generation, 4)

    def test_attempt_turn_and_flush_state_are_durable_and_target_scoped(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for index, target in enumerate(("thread-1", "thread-1", "thread-2"), start=1):
                _ = store.enqueue_queue_job(
                    db_path,
                    job_id=f"job-{index}",
                    target_thread_id=target,
                    channel_id=200 + index,
                    owner_user_id=7,
                    discord_message_id=900 + index,
                    app_server_generation=4,
                    prompt=f"request {index}",
                    queued=True,
                    ack_sent=True,
                    created_at=float(index),
                )
            started = store.begin_queue_job_attempt(
                db_path,
                "job-1",
                baseline_turn_ids=("turn-old",),
                app_server_generation=4,
            )
            running = store.mark_queue_job_running(
                db_path,
                "job-1",
                "turn-new",
                app_server_generation=4,
            )

            deleted = store.flush_queue_jobs(
                db_path,
                "thread-1",
                app_server_generation=4,
            )
            remaining = store.list_queue_jobs(db_path)

        self.assertEqual(started.attempt_count, 1)
        self.assertIs(started.state, QueueJobState.STARTING)
        self.assertEqual(started.baseline_turn_ids, ("turn-old",))
        self.assertIs(running.state, QueueJobState.RUNNING)
        self.assertEqual(running.turn_id, "turn-new")
        self.assertEqual([record.job_id for record in deleted], ["job-1", "job-2"])
        self.assertEqual([record.job_id for record in remaining], ["job-3"])

    def test_legacy_queue_schema_migrates_existing_rows_to_stale_generation_zero(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "CREATE TABLE codex_turn_queue ("
                    "job_id TEXT PRIMARY KEY, target_thread_id TEXT NOT NULL, "
                    "channel_id INTEGER NOT NULL, owner_user_id INTEGER, "
                    "discord_message_id INTEGER, prompt TEXT NOT NULL, queued INTEGER NOT NULL, "
                    "ack_sent INTEGER NOT NULL, state TEXT NOT NULL, attempt_count INTEGER NOT NULL, "
                    "turn_id TEXT, baseline_turn_ids TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '', "
                    "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
                )
                _ = conn.execute(
                    "INSERT INTO codex_turn_queue VALUES "
                    "('legacy', 'thread-1', 222, NULL, NULL, 'old', 1, 1, "
                    "'pending', 0, NULL, '[]', '', 1.0, 1.0)"
                )

            store.init_mirror_db(db_path)
            records = store.list_queue_jobs(db_path)
            with sqlite3.connect(db_path) as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(codex_turn_queue)").fetchall()
                }

        self.assertIn("app_server_generation", columns)
        self.assertEqual(records[0].app_server_generation, 0)

    def test_generation_discard_atomically_returns_deleted_rows(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for generation in (3, 4):
                _ = store.enqueue_queue_job(
                    db_path,
                    job_id=f"job-{generation}",
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    discord_message_id=900 + generation,
                    app_server_generation=generation,
                    prompt=f"request {generation}",
                    queued=True,
                    ack_sent=True,
                )

            stale = store.discard_queue_jobs_for_generation(db_path, 4)
            current = store.list_queue_jobs(db_path)
            unhealthy = store.discard_queue_jobs_for_generation(db_path, None)
            empty = store.list_queue_jobs(db_path)

        self.assertEqual([record.job_id for record in stale], ["job-3"])
        self.assertEqual([record.job_id for record in current], ["job-4"])
        self.assertEqual([record.job_id for record in unhealthy], ["job-4"])
        self.assertEqual(empty, [])

    def test_observed_discard_does_not_delete_rows_outside_observed_set(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for job_id, generation in (("job-old", 3), ("job-new", 4)):
                _ = store.enqueue_queue_job(
                    db_path,
                    job_id=job_id,
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    discord_message_id=None,
                    app_server_generation=generation,
                    prompt=job_id,
                    queued=True,
                    ack_sent=False,
                )

            observed = [
                record
                for record in store.list_queue_jobs(db_path)
                if record.job_id == "job-old"
            ]
            deleted = store.discard_observed_queue_jobs(db_path, observed)
            remaining = store.list_queue_jobs(db_path)

        self.assertEqual([record.job_id for record in deleted], ["job-old"])
        self.assertEqual([record.job_id for record in remaining], ["job-new"])

    def test_observed_discard_preserves_job_readopted_before_delete(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-recovered",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=999,
                app_server_generation=2,
                prompt="recover me",
                queued=True,
                ack_sent=True,
            )
            observation_ready = Event()
            adoption_done = Event()

            # Given: stale cleanup has observed generation 2 but has not deleted it.
            def cleanup_after_barrier() -> list[StoredQueueJob]:
                observed = store.list_queue_jobs(db_path)
                observation_ready.set()
                if not adoption_done.wait(timeout=5):
                    self.fail("generation adoption did not reach the cleanup barrier")
                return store.discard_observed_queue_jobs(db_path, observed)

            with ThreadPoolExecutor(max_workers=1) as executor:
                cleanup = executor.submit(cleanup_after_barrier)
                self.assertTrue(observation_ready.wait(timeout=5))

                # When: recovery adopts the same job before stale cleanup deletes it.
                _ = store.adopt_queue_jobs_generation(db_path, 4)
                adoption_done.set()
                deleted = cleanup.result(timeout=5)

            remaining = store.list_queue_jobs(db_path)

        # Then: the old observation cannot delete the newly adopted row.
        self.assertEqual(deleted, [])
        self.assertEqual([record.job_id for record in remaining], ["job-recovered"])
        self.assertEqual(remaining[0].app_server_generation, 4)


if __name__ == "__main__":
    _ = unittest.main()
