from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3

import codex_discord_store as store
from codex_discord_store_attempts import QueueAttemptState, QueueAttemptTransitionError
from codex_discord_store_queue import QueueJobState


class QueueAttemptStoreTests(unittest.TestCase):
    def test_attempt_records_request_write_boundary_and_terminal_turn(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=901,
                app_server_generation=4,
                prompt="request",
                queued=False,
                ack_sent=False,
            )

            pending = store.begin_queue_execution_attempt(
                db_path,
                "job-1",
                app_server_generation=4,
                baseline_turn_ids=("turn-old",),
            )
            prewrite = store.mark_queue_attempt_prewrite(
                db_path,
                pending.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            unknown = store.mark_queue_attempt_write_crossed(db_path, pending.attempt_id)
            running = store.mark_queue_execution_running(
                db_path,
                job_id="job-1",
                attempt_id=pending.attempt_id,
                app_server_generation=4,
                turn_id="turn-new",
            )
            terminal = store.mark_queue_attempt_terminal(db_path, pending.attempt_id)

        self.assertIs(pending.state, QueueAttemptState.EXEC_PENDING)
        self.assertIs(prewrite.state, QueueAttemptState.START_PREWRITE)
        self.assertEqual(prewrite.client_request_id, "request-1")
        self.assertEqual(prewrite.app_server_process_id, 4242)
        self.assertIs(unknown.state, QueueAttemptState.START_UNKNOWN)
        self.assertIs(running.state, QueueAttemptState.RUNNING)
        self.assertEqual(running.turn_id, "turn-new")
        self.assertIs(terminal.state, QueueAttemptState.TURN_TERMINAL)

    def test_completion_atomically_finalizes_attempt_inbox_and_queue(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="request",
                source="gateway",
                normalization_version=1,
            )
            promotion = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-a",
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                promotion.jobs[0].job_id,
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            _ = store.mark_queue_execution_running(
                db_path,
                job_id=promotion.jobs[0].job_id,
                attempt_id=attempt.attempt_id,
                app_server_generation=4,
                turn_id="turn-1",
            )

            store.complete_queue_execution_attempt(
                db_path,
                job_id=promotion.jobs[0].job_id,
                attempt_id=attempt.attempt_id,
            )

            inbox = store.list_deferred_discord_messages(db_path)
            attempts = store.list_queue_execution_attempts(db_path)
            jobs = store.list_queue_jobs(db_path)

        self.assertIs(inbox[0].state, store.DeferredInboxState.COMPLETED)
        self.assertIs(attempts[0].state, QueueAttemptState.TURN_TERMINAL)
        self.assertEqual(jobs, [])

    def test_generation_reconcile_adopts_only_provably_unwritten_work(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for job_id, message_id in (("safe", 901), ("unknown", 902)):
                _ = store.enqueue_queue_job(
                    db_path,
                    job_id=job_id,
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    discord_message_id=message_id,
                    app_server_generation=3,
                    prompt=job_id,
                    queued=False,
                    ack_sent=False,
                )
            safe = store.begin_queue_execution_attempt(
                db_path,
                "safe",
                app_server_generation=3,
                baseline_turn_ids=(),
            )
            unknown = store.begin_queue_execution_attempt(
                db_path,
                "unknown",
                app_server_generation=3,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                unknown.attempt_id,
                client_request_id="request-2",
                app_server_process_id=4242,
            )

            outcome = store.reconcile_queue_jobs_for_generation(db_path, 4)
            jobs = {job.job_id: job for job in store.list_queue_jobs(db_path)}
            attempts = {
                attempt.job_id: attempt
                for attempt in store.list_queue_execution_attempts(db_path)
            }

        self.assertEqual(outcome.adopted_job_ids, ("safe",))
        self.assertEqual(outcome.needs_review_job_ids, ("unknown",))
        self.assertEqual(jobs["safe"].app_server_generation, 4)
        self.assertEqual(jobs["unknown"].app_server_generation, 3)
        self.assertIs(attempts["safe"].state, QueueAttemptState.EXEC_PENDING)
        self.assertIs(attempts["unknown"].state, QueueAttemptState.NEEDS_REVIEW)

    def test_atomic_running_transition_rolls_back_both_rows_on_second_update_failure(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=901,
                app_server_generation=4,
                prompt="request",
                queued=False,
                ack_sent=False,
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                "job-1",
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "CREATE TRIGGER fail_running BEFORE UPDATE OF state ON codex_turn_queue "
                    "WHEN NEW.state = 'running' BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )

            with self.assertRaises(sqlite3.IntegrityError):
                _ = store.mark_queue_execution_running(
                    db_path,
                    job_id="job-1",
                    attempt_id=attempt.attempt_id,
                    app_server_generation=4,
                    turn_id="turn-1",
                )
            persisted_attempt = store.get_latest_queue_execution_attempt(db_path, "job-1")
            persisted_job = store.list_queue_jobs(db_path)[0]

        self.assertIsNotNone(persisted_attempt)
        assert persisted_attempt is not None
        self.assertIs(persisted_attempt.state, QueueAttemptState.START_UNKNOWN)
        self.assertIs(persisted_job.state, QueueJobState.STARTING)
        self.assertIsNone(persisted_job.turn_id)

    def test_atomic_running_transition_is_idempotent_only_for_the_same_turn(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=901,
                app_server_generation=4,
                prompt="request",
                queued=False,
                ack_sent=False,
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                "job-1",
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)

            first = store.mark_queue_execution_running(
                db_path,
                job_id="job-1",
                attempt_id=attempt.attempt_id,
                app_server_generation=4,
                turn_id="turn-1",
            )
            repeated = store.mark_queue_execution_running(
                db_path,
                job_id="job-1",
                attempt_id=attempt.attempt_id,
                app_server_generation=4,
                turn_id="turn-1",
            )
            with self.assertRaises(QueueAttemptTransitionError):
                _ = store.mark_queue_execution_running(
                    db_path,
                    job_id="job-1",
                    attempt_id=attempt.attempt_id,
                    app_server_generation=4,
                    turn_id="turn-conflict",
                )

        self.assertIs(first.state, QueueAttemptState.RUNNING)
        self.assertIs(repeated.state, QueueAttemptState.RUNNING)
        self.assertEqual(repeated.turn_id, "turn-1")

    def test_known_running_attempt_cannot_be_demoted_to_needs_review(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                discord_message_id=901,
                app_server_generation=4,
                prompt="request",
                queued=False,
                ack_sent=False,
            )
            attempt = store.begin_queue_execution_attempt(
                db_path,
                "job-1",
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            running = store.mark_queue_execution_running(
                db_path,
                job_id="job-1",
                attempt_id=attempt.attempt_id,
                app_server_generation=4,
                turn_id="turn-1",
            )

            with self.assertRaises(QueueAttemptTransitionError):
                _ = store.mark_queue_attempt_needs_review(
                    db_path,
                    running.attempt_id,
                    last_error="timeout",
                )

            persisted = store.get_latest_queue_execution_attempt(db_path, "job-1")

        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertIs(persisted.state, QueueAttemptState.RUNNING)
        self.assertEqual(persisted.turn_id, "turn-1")

    def test_exact_late_success_repairs_review_state_and_known_turn(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="request",
                source="gateway",
                normalization_version=1,
            )
            promotion = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-a",
            )
            job_id = promotion.jobs[0].job_id
            attempt = store.begin_queue_execution_attempt(
                db_path,
                job_id,
                app_server_generation=4,
                baseline_turn_ids=(),
            )
            _ = store.mark_queue_attempt_prewrite(
                db_path,
                attempt.attempt_id,
                client_request_id="request-1",
                app_server_process_id=4242,
            )
            _ = store.mark_queue_attempt_write_crossed(db_path, attempt.attempt_id)
            _ = store.mark_queue_attempt_needs_review(
                db_path,
                attempt.attempt_id,
                last_error="timeout",
            )

            mismatch = store.reconcile_late_queue_attempt_running(
                db_path,
                client_request_id="request-1",
                app_server_process_id=9999,
                app_server_generation=4,
                target_thread_id="thread-1",
                turn_id="turn-wrong",
            )
            reconciled = store.reconcile_late_queue_attempt_running(
                db_path,
                client_request_id="request-1",
                app_server_process_id=4242,
                app_server_generation=4,
                target_thread_id="thread-1",
                turn_id="turn-late",
            )
            adopted = store.reconcile_queue_jobs_for_generation(db_path, 5)
            persisted_attempt = store.get_latest_queue_execution_attempt(db_path, job_id)
            persisted_job = store.list_queue_jobs(db_path)[0]
            inbox = store.list_deferred_discord_messages(db_path)[0]

        self.assertFalse(mismatch)
        self.assertTrue(reconciled)
        assert reconciled is not None
        self.assertEqual(
            (reconciled.job_id, reconciled.target_thread_id, reconciled.channel_id),
            (job_id, "thread-1", 222),
        )
        self.assertEqual(adopted.adopted_job_ids, (job_id,))
        self.assertIsNotNone(persisted_attempt)
        assert persisted_attempt is not None
        self.assertIs(persisted_attempt.state, QueueAttemptState.RUNNING)
        self.assertEqual(persisted_attempt.turn_id, "turn-late")
        self.assertEqual(persisted_attempt.app_server_generation, 5)
        self.assertIs(persisted_job.state, QueueJobState.RUNNING)
        self.assertEqual(persisted_job.turn_id, "turn-late")
        self.assertEqual(persisted_job.app_server_generation, 5)
        self.assertIs(inbox.state, store.DeferredInboxState.PROMOTED)


if __name__ == "__main__":
    _ = unittest.main()
