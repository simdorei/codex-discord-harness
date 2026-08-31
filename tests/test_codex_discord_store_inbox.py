from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3

import codex_discord_store as store
from codex_discord_store_inbox import (
    DeferredInboxConflictError,
    DeferredInboxState,
)


class DeferredInboxStoreTests(unittest.TestCase):
    def test_same_target_channels_promote_independently_with_per_channel_fifo(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for message_id, channel_id, created_at in (
                (901, 222, 1.0),
                (902, 333, 2.0),
                (903, 222, 3.0),
                (904, 333, 4.0),
            ):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=message_id,
                    target_thread_id="thread-1",
                    channel_id=channel_id,
                    owner_user_id=7,
                    prompt=f"prompt-{message_id}",
                    source="gateway",
                    normalization_version=1,
                    created_at=created_at,
                )

            first_channel = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-a",
                now=10.0,
            )
            second_channel = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=333,
                app_server_generation=4,
                lease_owner="worker-b",
                now=10.0,
            )

        self.assertEqual(
            [job.discord_message_id for job in first_channel.jobs],
            [901, 903],
        )
        self.assertEqual(
            [job.discord_message_id for job in second_channel.jobs],
            [902, 904],
        )
        self.assertEqual(first_channel.lease_epoch, 1)
        self.assertEqual(second_channel.lease_epoch, 1)

    def test_processed_claim_failure_rolls_back_inbox_insert(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.init_mirror_db(db_path)
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute(
                    "CREATE TRIGGER reject_processed_claim BEFORE INSERT ON discord_processed_messages "
                    "BEGIN SELECT RAISE(ABORT, 'processed claim rejected'); END"
                )

            with self.assertRaises(sqlite3.IntegrityError):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=901,
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    prompt="keep this prompt",
                    source="gateway",
                    normalization_version=1,
                )

            self.assertEqual(store.list_deferred_discord_messages(db_path), [])

    def test_claim_is_atomic_with_processed_message_ownership(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"

            claim = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="keep this prompt",
                source="gateway",
                normalization_version=1,
                created_at=10.0,
            )
            duplicate = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="keep this prompt",
                source="gateway",
                normalization_version=1,
                created_at=11.0,
            )
            records = store.list_deferred_discord_messages(db_path)

            self.assertTrue(store.is_processed_discord_message_id(db_path, 901))

        self.assertTrue(claim.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].prompt, "keep this prompt")
        self.assertIs(records[0].state, DeferredInboxState.RECEIVED)

    def test_duplicate_message_id_with_different_immutable_payload_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=901,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="original",
                source="gateway",
                normalization_version=1,
            )

            with self.assertRaises(DeferredInboxConflictError):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=901,
                    target_thread_id="thread-2",
                    channel_id=333,
                    owner_user_id=8,
                    prompt="changed",
                    source="gateway",
                    normalization_version=1,
                )

            records = store.list_deferred_discord_messages(db_path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target_thread_id, "thread-1")
        self.assertEqual(records[0].prompt, "original")

    def test_fifo_promotion_uses_target_lease_and_message_id_job_key(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            for message_id, created_at in ((902, 20.0), (901, 10.0)):
                _ = store.claim_deferred_discord_message(
                    db_path,
                    message_id=message_id,
                    target_thread_id="thread-1",
                    channel_id=222,
                    owner_user_id=7,
                    prompt=f"prompt-{message_id}",
                    source="gateway",
                    normalization_version=1,
                    created_at=created_at,
                )

            first = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-a",
                now=30.0,
                lease_seconds=10.0,
            )
            duplicate = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-b",
                now=31.0,
                lease_seconds=10.0,
            )
            _ = store.claim_deferred_discord_message(
                db_path,
                message_id=903,
                target_thread_id="thread-1",
                channel_id=222,
                owner_user_id=7,
                prompt="prompt-903",
                source="gateway",
                normalization_version=1,
                created_at=32.0,
            )
            next_owner = store.promote_deferred_discord_messages(
                db_path,
                target_thread_id="thread-1",
                channel_id=222,
                app_server_generation=4,
                lease_owner="worker-b",
                now=32.0,
                lease_seconds=10.0,
            )
            queue_records = store.list_queue_jobs(db_path)
            inbox_records = store.list_deferred_discord_messages(db_path)

        self.assertEqual([job.discord_message_id for job in first.jobs], [901, 902])
        self.assertEqual([job.job_id for job in first.jobs], ["discord:901", "discord:902"])
        self.assertGreater(first.lease_epoch, 0)
        self.assertEqual(duplicate.jobs, ())
        self.assertEqual([job.discord_message_id for job in next_owner.jobs], [903])
        self.assertGreater(next_owner.lease_epoch, first.lease_epoch)
        self.assertEqual(len(queue_records), 3)
        self.assertTrue(all(row.state is DeferredInboxState.PROMOTED for row in inbox_records))


if __name__ == "__main__":
    _ = unittest.main()
