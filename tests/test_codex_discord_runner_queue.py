from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from contextlib import suppress
from dataclasses import dataclass
from typing import cast, override
import unittest

import codex_discord_runner as discord_runner
import codex_discord_runner_queue as runner_queue
from codex_app_server_transport_turn_outcomes import TurnCompletion, TurnStatus
from codex_discord_queue_processor import (
    QueueAttempt,
    QueueGenerationExpiredError,
    QueueGenerationRecovery,
    QueueJobSummary,
    QueueTurnCoordinatorDeps,
)
from codex_discord_runtime import normalize_runner_key


@dataclass(frozen=True, slots=True)
class FakeChannel:
    id: int

    async def send(self, _text: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class FakeAuthor:
    id: int


@dataclass(frozen=True, slots=True)
class FakeMessage:
    author: FakeAuthor


class RunnerQueueTests(unittest.IsolatedAsyncioTestCase):
    @override
    async def asyncTearDown(self) -> None:
        async with runner_queue.THREAD_RUNNERS_LOCK:
            runner_queue.THREAD_RUNNERS.clear()

    async def test_enqueue_creates_runner_and_reports_busy(self) -> None:
        calls: list[str | None] = []

        async def fake_loop(target_thread_id: str | None) -> None:
            calls.append(target_thread_id)

        channel = FakeChannel(id=222)
        source_message = FakeMessage(author=FakeAuthor(id=7))

        size = await runner_queue.enqueue_thread_ask(
            channel,
            "please queue",
            "thread-1",
            queued=True,
            ack_sent=True,
            source_message=source_message,
            thread_runner_loop_func=fake_loop,
        )
        runner = await runner_queue.get_thread_runner("thread-1")
        task = runner["task"]
        if task is not None:
            await task

        self.assertEqual(size, 1)
        self.assertEqual(calls, ["thread-1"])
        self.assertTrue(await runner_queue.is_thread_runner_busy("thread-1"))

    async def test_retract_removes_latest_matching_queued_job(self) -> None:
        channel = FakeChannel(id=222)
        other_channel = FakeChannel(id=333)
        owner_message = FakeMessage(author=FakeAuthor(id=7))
        other_owner_message = FakeMessage(author=FakeAuthor(id=9))
        runner = await runner_queue.get_thread_runner("thread-1")
        queue = runner["queue"]
        jobs: list[runner_queue.QueueJob] = [
            {"channel": channel, "prompt": "first matching", "source_message": owner_message},
            {"channel": channel, "prompt": "other owner", "source_message": other_owner_message},
            {"channel": channel, "prompt": "latest matching", "source_message": owner_message},
            {"channel": other_channel, "prompt": "other channel", "source_message": owner_message},
        ]
        for job in jobs:
            await queue.put(job)

        result = await runner_queue.retract_thread_ask(
            "thread-1",
            channel_id=222,
            owner_user_id=7,
        )

        remaining_prompts: list[str] = []
        while not queue.empty():
            job = queue.get_nowait()
            remaining_prompts.append(str(job.get("prompt")))
            queue.task_done()

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["remaining"], 3)
        self.assertEqual(
            remaining_prompts,
            ["first matching", "other owner", "other channel"],
        )

    async def test_retract_missing_runner_reports_normalized_key(self) -> None:
        result = await runner_queue.retract_thread_ask("thread-1")

        self.assertEqual(
            result,
            {
                "removed": 0,
                "remaining": 0,
                "active": False,
                "target_key": normalize_runner_key("thread-1"),
            },
        )

    async def test_generation_recovery_drain_rebuilds_all_durable_jobs_from_db(self) -> None:
        queue: asyncio.Queue[runner_queue.QueueItem] = asyncio.Queue()
        for job_id, generation in (("old", 2), ("current", 3), ("legacy", 0)):
            await queue.put(
                {
                    "job_id": job_id,
                    "app_server_generation": generation,
                    "prompt": job_id,
                }
            )
        runner: runner_queue.ThreadRunner = {
            "queue": queue,
            "task": None,
            "active": True,
            "target_thread_id": "thread-1",
            "queued_job_ids": {"old", "current", "legacy"},
        }

        drained = discord_runner._drain_durable_queue_for_generation_recovery(queue, runner)
        self.assertTrue(queue.empty())
        retained_ids = [str(job.get("job_id")) for job in drained]

        self.assertEqual(retained_ids, ["old", "current", "legacy"])
        self.assertEqual(runner["queued_job_ids"], set())

    async def test_generation_expiry_requeues_reconciled_job_without_bot_restart(self) -> None:
        completed = asyncio.Event()
        acquire_generations: list[int] = []

        async def acquire_turn(
            job: runner_queue.QueueJob,
            _prompt: str,
            _target: str | None,
            *,
            recovery: bool,
        ) -> QueueAttempt:
            _ = recovery
            generation = int(job.get("app_server_generation") or 0)
            acquire_generations.append(generation)
            if generation == 2:
                raise QueueGenerationExpiredError(
                    stage="test",
                    expected_generation=2,
                    current_generation=3,
                    healthy=True,
                )
            return QueueAttempt(1, "thread-1", "turn-1", generation)

        async def wait_for_completion(
            _thread_id: str,
            _turn_id: str,
            _generation: int,
        ) -> TurnCompletion:
            return TurnCompletion("thread-1", "turn-1", TurnStatus.COMPLETED)

        async def complete_job(_job: runner_queue.QueueJob) -> None:
            completed.set()

        async def recover(
            jobs: tuple[runner_queue.QueueJob, ...],
        ) -> QueueGenerationRecovery:
            recovered = cast(runner_queue.QueueJob, cast(object, dict(jobs[0])))
            recovered["app_server_generation"] = 3
            return QueueGenerationRecovery((recovered,), ())

        async def no_summaries(
            _job: runner_queue.QueueJob,
            _target: str | None,
        ) -> list[QueueJobSummary]:
            return []

        async def report_retry(_job: runner_queue.QueueJob, _reason: str) -> None:
            return None

        async def report_batch_failure(
            _job: runner_queue.QueueJob,
            _reason: str,
            _summaries: list[QueueJobSummary],
        ) -> None:
            return None

        async def report_job_failed(
            _job: runner_queue.QueueJob,
            _target: str | None,
        ) -> None:
            return None

        async def send_text(*_args: object, **_kwargs: object) -> int:
            return 1

        async def wait_idle(_target: str | None) -> tuple[str, str | None, str]:
            return "idle", "thread-1", "thread-1"

        local_runners: runner_queue.RunnerMap = {}
        local_lock = asyncio.Lock()

        async def get_runner(target: str | None) -> runner_queue.ThreadRunner:
            return await runner_queue.get_thread_runner(
                target,
                runners=local_runners,
                runners_lock=local_lock,
            )

        runner = await get_runner("thread-1")
        await runner["queue"].put(
            {
                "job_id": "job-1",
                "channel": FakeChannel(222),
                "target_thread_id": "thread-1",
                "prompt": "recover",
                "app_server_generation": 2,
            }
        )
        runner["queued_job_ids"] = {"job-1"}
        deps = QueueTurnCoordinatorDeps(
            acquire_turn=acquire_turn,
            wait_for_turn_completion=wait_for_completion,
            complete_job=complete_job,
            flush_jobs=no_summaries,
            report_retry=report_retry,
            report_batch_failure=report_batch_failure,
            log=lambda _message: None,
        )
        task = asyncio.create_task(
            discord_runner.thread_runner_loop(
                "thread-1",
                get_busy_state_func=lambda _target: ("idle", "thread-1", "thread-1"),
                wait_for_idle_func=wait_idle,
                queue_coordinator_deps=deps,
                recover_generation_expired_func=recover,
                report_job_failed_func=report_job_failed,
                send_text_func=send_text,
                log_func=lambda _message: None,
                runners=local_runners,
                runners_lock=local_lock,
                get_thread_runner_func=get_runner,
            )
        )
        try:
            await asyncio.wait_for(completed.wait(), timeout=1.0)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        self.assertEqual(acquire_generations, [2, 3])
