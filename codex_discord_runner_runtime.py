"""Runtime wiring for Discord runner queues."""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TypeAlias

import codex_discord_queue_messages as discord_queue_messages
import codex_discord_queue_targets as discord_queue_targets
import codex_discord_durable_queue_runtime as durable_queue_runtime
import codex_discord_message_dispatch_runtime as message_dispatch_runtime
import codex_discord_runner as discord_runner
import codex_discord_runtime as discord_runtime
import codex_discord_store as store
from codex_discord_store_inbox import DeferredInboxState
from codex_discord_store_queue import StoredQueueJob
from codex_discord_runner_queue import QueueJobValue, RunnerMap, ThreadRunner
from codex_discord_runner_queue import QueueJob
from codex_discord_durable_queue_restore import QueueRestoreBot, QueueRestoreUnstableError
from codex_discord_queue_job_memory import to_memory_queue_job


QueueRetractResult: TypeAlias = dict[str, int | bool | str]
BuildRunnersMessageFunc: TypeAlias = Callable[[], Awaitable[str]]
FormatTargetRefForLogFunc: TypeAlias = Callable[[str], str]
GetQueueTargetBridgeFunc: TypeAlias = Callable[[], discord_queue_targets.QueueTargetBridge]
SnapshotThreadRunnersFunc: TypeAlias = Callable[[], dict[str, discord_runtime.RunnerState]]


@dataclass(frozen=True, slots=True)
class RunnerRuntimeDeps:
    thread_runners: RunnerMap
    thread_runners_lock: asyncio.Lock
    runner_snapshot_lock: discord_runtime.RunnerLockLike
    snapshot_thread_runners: SnapshotThreadRunnersFunc
    get_runtime_state: Callable[[], discord_runtime.DiscordRuntimeState]
    get_busy_state_for_thread: discord_runner.GetBusyStateFunc
    resolve_target_ref: discord_runtime.ResolveTargetRefFunc
    get_queue_target_bridge: GetQueueTargetBridgeFunc
    get_mirrored_codex_thread_id: discord_queue_targets.GetMirroredCodexThreadIdFunc
    format_target_ref_for_log: FormatTargetRefForLogFunc
    durable_queue: durable_queue_runtime.DurableQueueRuntime
    send_chunks: discord_runner.SendTextFunc
    log: discord_runner.LogFunc


@dataclass(frozen=True, slots=True)
class RunnerRuntime:
    deps: RunnerRuntimeDeps
    _deferred_tasks: dict[tuple[str, int], asyncio.Task[None]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    _deferred_wake_generations: dict[tuple[str, int], int] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )
    _restore_retry_tasks: dict[int, asyncio.Task[None]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )

    async def defer_plain_ask(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str,
        *,
        source_message: QueueJobValue,
    ) -> bool:
        task = asyncio.create_task(
            self._defer_plain_ask_owned(
                channel,
                prompt,
                target_thread_id,
                source_message=source_message,
            )
        )
        return await asyncio.shield(task)

    async def _defer_plain_ask_owned(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str,
        *,
        source_message: QueueJobValue,
    ) -> bool:
        try:
            intake = await self.deps.durable_queue.intake_deferred(
                channel,
                prompt,
                target_thread_id,
                source_message=source_message,
            )
        except Exception as exc:
            raise message_dispatch_runtime.ReplayableMessageIntakeError(
                "Deferred Discord inbox intake did not commit."
            ) from exc
        if intake is None:
            raise message_dispatch_runtime.ReplayableMessageIntakeError(
                "Deferred Discord inbox intake could not identify the Discord message."
            )
        self._ensure_deferred_replay_task(
            target_thread_id,
            channel=channel,
        )
        await self._enqueue_promoted_jobs(
            intake.jobs,
            channel=channel,
            source_message=source_message,
        )
        if intake.pending:
            if intake.inbox.state is DeferredInboxState.RECEIVED:
                try:
                    _ = await self.deps.send_chunks(
                        channel,
                        "Codex is temporarily unavailable. Your message was saved and will be replayed automatically.",
                        context="deferred_discord_inbox_saved",
                    )
                except Exception as exc:
                    self.deps.log(
                        "deferred_inbox_saved_notice_failed "
                        + f"message={intake.inbox.message_id} "
                        + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                    )
        return True

    async def _enqueue_promoted_jobs(
        self,
        records: tuple[StoredQueueJob, ...],
        *,
        channel: QueueJobValue,
        source_message: QueueJobValue,
    ) -> None:
        source_id = int(getattr(source_message, "id", 0) or 0)
        for record in records:
            job = to_memory_queue_job(
                record,
                channel,
                source_message if record.discord_message_id == source_id else None,
            )
            _ = await discord_runner.enqueue_existing_thread_ask(
                job,
                record.target_thread_id,
                get_thread_runner_func=self.get_thread_runner,
                thread_runner_loop_func=self.thread_runner_loop,
            )

    def _ensure_deferred_replay_task(
        self,
        target_thread_id: str,
        *,
        channel: QueueJobValue,
    ) -> None:
        channel_id = int(getattr(channel, "id", 0) or 0)
        task_key = (target_thread_id, channel_id)
        self._deferred_wake_generations[task_key] = (
            self._deferred_wake_generations.get(task_key, 0) + 1
        )
        existing = self._deferred_tasks.get(task_key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._deferred_replay_loop(target_thread_id, channel=channel)
        )
        self._deferred_tasks[task_key] = task
        task.add_done_callback(
            lambda completed: self._deferred_replay_task_done(
                task_key,
                target_thread_id,
                channel,
                completed,
            )
        )

    def wake_reconciled_queue_job(
        self,
        reconciliation: store.LateQueueAttemptReconciliation,
        job: QueueJob,
    ) -> None:
        channel = job.get("channel")
        channel_id = int(getattr(channel, "id", 0) or 0)
        if channel is None or channel_id != reconciliation.channel_id:
            self.deps.log(
                "queue_turn_late_start_wakeup_rejected "
                + f"job={reconciliation.job_id} expected_channel={reconciliation.channel_id} "
                + f"actual_channel={channel_id or '-'}"
            )
            return
        self._ensure_deferred_replay_task(
            reconciliation.target_thread_id,
            channel=channel,
        )
        self.deps.log(
            "queue_turn_late_start_wakeup_started "
            + f"job={reconciliation.job_id} target={reconciliation.target_thread_id} "
            + f"channel={reconciliation.channel_id}"
        )

    def _deferred_replay_task_done(
        self,
        task_key: tuple[str, int],
        target_thread_id: str,
        channel: QueueJobValue,
        task: asyncio.Task[None],
    ) -> None:
        if self._deferred_tasks.get(task_key) is task:
            self._deferred_tasks.pop(task_key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self.deps.log(
            "deferred_replay_task_failed "
            + f"target={target_thread_id} channel={task_key[1]} "
            + f"error_type={type(error).__name__} error={str(error)[:300]}"
        )
        _ = asyncio.create_task(
            self._restart_deferred_replay_task(
                target_thread_id,
                channel=channel,
            )
        )

    async def _restart_deferred_replay_task(
        self,
        target_thread_id: str,
        *,
        channel: QueueJobValue,
    ) -> None:
        await asyncio.sleep(0.5)
        self._ensure_deferred_replay_task(target_thread_id, channel=channel)

    async def _deferred_replay_loop(
        self,
        target_thread_id: str,
        *,
        channel: QueueJobValue,
    ) -> None:
        channel_id = int(getattr(channel, "id", 0) or 0)
        task_key = (target_thread_id, channel_id)
        try:
            while True:
                records = await self.deps.durable_queue.promote_deferred_target(
                    target_thread_id,
                    channel_id=channel_id,
                )
                await self._enqueue_promoted_jobs(
                    records,
                    channel=channel,
                    source_message=None,
                )
                executable = await self.deps.durable_queue.reconcile_deferred_target(
                    target_thread_id,
                    channel_id=channel_id,
                )
                await self._enqueue_promoted_jobs(
                    executable,
                    channel=channel,
                    source_message=None,
                )
                wake_generation = self._deferred_wake_generations.get(task_key, 0)
                pending = await asyncio.to_thread(
                    store.has_pending_deferred_discord_messages,
                    self.deps.durable_queue.deps.get_db_path(),
                    target_thread_id,
                    channel_id,
                )
                snapshot = self.deps.durable_queue.deps.get_app_server_lifecycle()
                queued = await asyncio.to_thread(
                    store.has_executable_queue_jobs_for_target_channel,
                    self.deps.durable_queue.deps.get_db_path(),
                    target_thread_id=target_thread_id,
                    channel_id=channel_id,
                    app_server_generation=(
                        snapshot.generation if snapshot.healthy else None
                    ),
                )
                if (
                    not pending
                    and not queued
                    and self._deferred_wake_generations.get(task_key, 0)
                    == wake_generation
                ):
                    return
                await asyncio.sleep(0.5)
        finally:
            if self._deferred_tasks.get(task_key) is asyncio.current_task():
                self._deferred_tasks.pop(task_key, None)

    async def build_runners_message(self) -> str:
        async with self.deps.thread_runners_lock:
            runners_snapshot = self.deps.snapshot_thread_runners()
        return await discord_runtime.build_runners_message(
            runners_snapshot,
            self.deps.runner_snapshot_lock,
            resolve_target_ref_func=self.deps.resolve_target_ref,
        )

    def resolve_queue_command_target(
        self,
        channel_id: int | None,
        ref: str | None,
    ) -> tuple[str | None, str]:
        return discord_queue_targets.resolve_queue_command_target(
            channel_id,
            ref,
            bridge_module=self.deps.get_queue_target_bridge(),
            resolve_target_ref_func=self.deps.resolve_target_ref,
            get_mirrored_codex_thread_id_func=self.deps.get_mirrored_codex_thread_id,
        )

    async def retract_queued_ask_for_request(
        self,
        *,
        channel_id: int | None,
        user_id: int | None,
        ref: str | None,
    ) -> tuple[str, QueueRetractResult]:
        target_thread_id, target_ref = self.resolve_queue_command_target(channel_id, ref)
        result = await self.retract_thread_ask(
            target_thread_id,
            channel_id=channel_id,
            owner_user_id=user_id,
        )
        self.deps.log(
            " ".join(
                [
                    f"queue_retract user={user_id or '-'}",
                    f"target={target_thread_id or '-'}",
                    f"target_ref={self.deps.format_target_ref_for_log(target_ref)}",
                    f"removed={int(result.get('removed') or 0)}",
                    f"remaining={int(result.get('remaining') or 0)}",
                    f"active={bool(result.get('active'))}",
                ]
            )
        )
        return discord_queue_messages.build_retract_message(result, target_ref), result

    @asynccontextmanager
    async def codex_app_turn_slot(self, target_thread_id: str | None) -> AsyncGenerator[bool]:
        async with discord_runtime.codex_app_turn_slot(
            self.deps.get_runtime_state(),
            target_thread_id,
            log=self.deps.log,
        ) as waited:
            yield waited

    async def get_thread_runner(self, target_thread_id: str | None) -> ThreadRunner:
        return await discord_runner.get_thread_runner(
            target_thread_id,
            runners=self.deps.thread_runners,
            runners_lock=self.deps.thread_runners_lock,
            normalize_runner_key_func=discord_runtime.normalize_runner_key,
        )

    async def wait_for_codex_thread_idle(
        self,
        target_thread_id: str | None,
        *,
        timeout_sec: float = 3600.0,
        poll_sec: float = 5.0,
    ) -> tuple[str, str | None, str]:
        return await discord_runner.wait_for_codex_thread_idle(
            target_thread_id,
            get_busy_state_func=self.deps.get_busy_state_for_thread,
            timeout_sec=timeout_sec,
            poll_sec=poll_sec,
        )

    async def enqueue_thread_ask(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str | None,
        *,
        queued: bool = False,
        ack_sent: bool = False,
        source_message: QueueJobValue = None,
    ) -> int:
        if target_thread_id:
            job, _created, position = await self.deps.durable_queue.enqueue(
                channel,
                prompt,
                target_thread_id,
                queued=queued,
                ack_sent=ack_sent,
                source_message=source_message,
            )
            _ = await discord_runner.enqueue_existing_thread_ask(
                job,
                target_thread_id,
                get_thread_runner_func=self.get_thread_runner,
                thread_runner_loop_func=self.thread_runner_loop,
            )
            return position
        return await discord_runner.enqueue_thread_ask(
            channel,
            prompt,
            target_thread_id,
            queued=queued,
            ack_sent=ack_sent,
            source_message=source_message,
            get_thread_runner_func=self.get_thread_runner,
            thread_runner_loop_func=self.thread_runner_loop,
        )

    async def retract_thread_ask(
        self,
        target_thread_id: str | None,
        *,
        channel_id: int | None = None,
        owner_user_id: int | None = None,
    ) -> QueueRetractResult:
        if target_thread_id:
            record = await self.deps.durable_queue.retract_job(
                target_thread_id,
                channel_id=channel_id,
                owner_user_id=owner_user_id,
            )
            if record is not None:
                result = await discord_runner.retract_thread_ask(
                    target_thread_id,
                    job_id=record.job_id,
                    runners=self.deps.thread_runners,
                    runners_lock=self.deps.thread_runners_lock,
                    normalize_runner_key_func=discord_runtime.normalize_runner_key,
                )
                result["removed"] = 1
                return result
        return await discord_runner.retract_thread_ask(
            target_thread_id,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
            runners=self.deps.thread_runners,
            runners_lock=self.deps.thread_runners_lock,
            normalize_runner_key_func=discord_runtime.normalize_runner_key,
        )

    async def report_thread_runner_job_failed(
        self,
        job: QueueJob,
        target_thread_id: str | None,
    ) -> None:
        await discord_runner.report_thread_runner_job_failed(
            job,
            target_thread_id,
            send_text_func=self.deps.send_chunks,
            log_func=self.deps.log,
        )

    async def thread_runner_loop(self, target_thread_id: str | None) -> None:
        await discord_runner.thread_runner_loop(
            target_thread_id,
            runners=self.deps.thread_runners,
            runners_lock=self.deps.thread_runners_lock,
            normalize_runner_key_func=discord_runtime.normalize_runner_key,
            get_thread_runner_func=self.get_thread_runner,
            get_busy_state_func=self.deps.get_busy_state_for_thread,
            wait_for_idle_func=self.wait_for_codex_thread_idle,
            queue_coordinator_deps=self.deps.durable_queue.coordinator_deps(),
            recover_generation_expired_func=(
                self.deps.durable_queue.recover_generation_expired_jobs
            ),
            report_job_failed_func=self.report_thread_runner_job_failed,
            send_text_func=self.deps.send_chunks,
            log_func=self.deps.log,
        )

    async def restore_durable_queue_runners(
        self,
        bot: QueueRestoreBot,
    ) -> int:
        try:
            return await self._restore_durable_queue_runners_once(bot)
        except QueueRestoreUnstableError as exc:
            self.deps.log(
                "queue_restore_deferred "
                + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
            )
            self._ensure_restore_retry_task(bot)
            return 0

    async def _restore_durable_queue_runners_once(
        self,
        bot: QueueRestoreBot,
    ) -> int:
        deferred = await self.deps.durable_queue.restore_deferred_inbox(bot)
        try:
            restored_jobs = await self.deps.durable_queue.restore_jobs(bot)
        except QueueRestoreUnstableError:
            for seed in deferred.seeds:
                self._ensure_deferred_replay_task(
                    seed.target_thread_id,
                    channel=seed.channel,
                )
            raise
        jobs_by_id = {
            str(job.get("job_id") or ""): job
            for job in (*restored_jobs, *deferred.jobs)
            if str(job.get("job_id") or "")
        }
        jobs = list(jobs_by_id.values())
        for job in jobs:
            target_thread_id = str(job.get("target_thread_id") or "").strip() or None
            _ = await discord_runner.enqueue_existing_thread_ask(
                job,
                target_thread_id,
                get_thread_runner_func=self.get_thread_runner,
                thread_runner_loop_func=self.thread_runner_loop,
            )
        for seed in deferred.seeds:
            self._ensure_deferred_replay_task(
                seed.target_thread_id,
                channel=seed.channel,
            )
        self.deps.log(f"queue_restore_done jobs={len(jobs)}")
        return len(jobs)

    def _ensure_restore_retry_task(self, bot: QueueRestoreBot) -> None:
        task_key = id(bot)
        existing = self._restore_retry_tasks.get(task_key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._retry_durable_queue_restore(bot))
        self._restore_retry_tasks[task_key] = task
        task.add_done_callback(
            lambda completed: self._restore_retry_task_done(task_key, completed)
        )

    async def _retry_durable_queue_restore(self, bot: QueueRestoreBot) -> None:
        while True:
            await asyncio.sleep(1.0)
            try:
                restored = await self._restore_durable_queue_runners_once(bot)
            except QueueRestoreUnstableError as exc:
                self.deps.log(
                    "queue_restore_retry_pending "
                    + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                )
                continue
            self.deps.log(f"queue_restore_retry_done jobs={restored}")
            return

    def _restore_retry_task_done(
        self,
        task_key: int,
        task: asyncio.Task[None],
    ) -> None:
        if self._restore_retry_tasks.get(task_key) is task:
            self._restore_retry_tasks.pop(task_key, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.deps.log(
                "queue_restore_retry_failed "
                + f"error_type={type(error).__name__} error={str(error)[:300]}"
            )
