from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest

from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
import codex_discord_durable_queue_restore as queue_restore
import codex_discord_store as store
from codex_discord_runner_queue import QueueJobValue


@dataclass(frozen=True, slots=True)
class _Channel:
    id: int


@dataclass(frozen=True, slots=True)
class _Deps:
    db_path: Path
    lifecycle: Callable[[], AppServerLifecycleSnapshot]
    logs: list[str]
    ensure_ready: Callable[[], None] = lambda: None

    @property
    def get_db_path(self) -> Callable[[], Path]:
        return lambda: self.db_path

    @property
    def get_app_server_lifecycle(self) -> Callable[[], AppServerLifecycleSnapshot]:
        return self.lifecycle

    @property
    def ensure_app_server_ready(self) -> Callable[[], None]:
        return self.ensure_ready

    @property
    def log(self) -> Callable[[str], None]:
        return self.logs.append


@dataclass(frozen=True, slots=True)
class _Bot:
    channel: _Channel

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]:
        return (self.channel, "cache") if channel_id == self.channel.id else (None, "miss")

    async def fetch_channel(self, channel_id: int) -> QueueJobValue:
        _ = channel_id
        return self.channel

    def is_allowed_message_channel(self, channel: QueueJobValue) -> bool:
        return channel is self.channel


class DurableQueueRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_adopts_persisted_jobs_into_the_new_unique_generation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "queue.db"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=101,
                owner_user_id=202,
                discord_message_id=303,
                app_server_generation=41,
                prompt="continue",
                queued=True,
                ack_sent=True,
            )
            snapshot = AppServerLifecycleSnapshot(99, True, 1234.0)
            deps = _Deps(db_path, lambda: snapshot, [])

            jobs = await queue_restore.restore_queue_jobs(_Bot(_Channel(101)), deps)

            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].get("job_id"), "job-1")
            self.assertEqual(jobs[0].get("app_server_generation"), 99)
            records = store.list_queue_jobs(db_path)
            self.assertEqual([record.app_server_generation for record in records], [99])
            self.assertTrue(any("queue_restore_adopted" in line for line in deps.logs))

    async def test_unhealthy_start_is_retried_and_persisted_job_is_restored(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "queue.db"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=101,
                owner_user_id=202,
                discord_message_id=303,
                app_server_generation=41,
                prompt="continue",
                queued=True,
                ack_sent=True,
            )
            current = [AppServerLifecycleSnapshot(41, False, None)]
            starts: list[bool] = []

            def ensure_ready() -> None:
                starts.append(True)
                current[0] = AppServerLifecycleSnapshot(99, True, 1234.0)

            deps = _Deps(db_path, lambda: current[0], [], ensure_ready)

            jobs = await queue_restore.restore_queue_jobs(_Bot(_Channel(101)), deps)

            self.assertEqual(starts, [True])
            self.assertEqual([job.get("job_id") for job in jobs], ["job-1"])
            records = store.list_queue_jobs(db_path)
            self.assertEqual([record.app_server_generation for record in records], [99])

    async def test_generation_change_during_channel_resolution_is_readopted(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "queue.db"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=101,
                owner_user_id=202,
                discord_message_id=303,
                app_server_generation=41,
                prompt="continue",
                queued=True,
                ack_sent=True,
            )
            current = [AppServerLifecycleSnapshot(99, True, 1234.0)]

            class GenerationChangingBot(_Bot):
                def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]:
                    if current[0].generation == 99:
                        current[0] = AppServerLifecycleSnapshot(100, True, 1235.0)
                    return super().get_cached_channel_or_thread(channel_id)

            deps = _Deps(db_path, lambda: current[0], [])

            jobs = await queue_restore.restore_queue_jobs(
                GenerationChangingBot(_Channel(101)),
                deps,
            )

            self.assertEqual([job.get("job_id") for job in jobs], ["job-1"])
            self.assertEqual(jobs[0].get("app_server_generation"), 100)
            records = store.list_queue_jobs(db_path)
            self.assertEqual([record.app_server_generation for record in records], [100])
            self.assertTrue(any("queue_restore_generation_changed" in line for line in deps.logs))

    async def test_repeated_start_failure_is_surfaced_without_deleting_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "queue.db"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=101,
                owner_user_id=202,
                discord_message_id=303,
                app_server_generation=41,
                prompt="continue",
                queued=True,
                ack_sent=True,
            )

            def fail_start() -> None:
                raise OSError("start unavailable")

            deps = _Deps(
                db_path,
                lambda: AppServerLifecycleSnapshot(41, False, None),
                [],
                fail_start,
            )

            with self.assertRaisesRegex(queue_restore.QueueRestoreUnstableError, "start unavailable"):
                _ = await queue_restore.restore_queue_jobs(_Bot(_Channel(101)), deps)

            self.assertEqual([record.job_id for record in store.list_queue_jobs(db_path)], ["job-1"])

    async def test_repeated_healthy_generation_changes_surface_error_and_preserve_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "queue.db"
            _ = store.enqueue_queue_job(
                db_path,
                job_id="job-1",
                target_thread_id="thread-1",
                channel_id=101,
                owner_user_id=202,
                discord_message_id=303,
                app_server_generation=41,
                prompt="continue",
                queued=True,
                ack_sent=True,
            )
            generation = [99]

            class AlwaysChangingBot(_Bot):
                def get_cached_channel_or_thread(self, channel_id: int) -> tuple[QueueJobValue, str]:
                    generation[0] += 1
                    return super().get_cached_channel_or_thread(channel_id)

            deps = _Deps(
                db_path,
                lambda: AppServerLifecycleSnapshot(generation[0], True, 1234.0),
                [],
            )

            with self.assertRaises(queue_restore.QueueRestoreUnstableError):
                _ = await queue_restore.restore_queue_jobs(
                    AlwaysChangingBot(_Channel(101)),
                    deps,
                )

            records = store.list_queue_jobs(db_path)
            self.assertEqual([record.job_id for record in records], ["job-1"])
            self.assertEqual(records[0].app_server_generation, 101)


if __name__ == "__main__":
    _ = unittest.main()
