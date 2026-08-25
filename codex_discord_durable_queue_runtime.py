from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
import uuid

from codex_app_server_transport_lifecycle import (
    AppServerGenerationMismatch,
    AppServerLifecycleSnapshot,
)
from codex_app_server_transport_attempt_context import bind_turn_start_attempt
from codex_app_server_transport_turn_outcomes import (
    TurnCompletion,
    TurnCompletionFound,
    TurnCompletionObservation,
    TurnCompletionTransportError,
    TurnStatus,
)
import codex_discord_prompt_delivery_prepare as prompt_delivery_prepare
from codex_discord_durable_queue_restore import QueueRestoreBot, restore_queue_jobs
import codex_discord_queue_generation as queue_generation
from codex_discord_queue_job_memory import (
    copy_stored_queue_state,
    queue_job_int,
    to_memory_queue_job,
)
from codex_discord_queue_reporting import (
    build_queue_batch_failure_message,
    build_queue_retry_message,
)
from codex_discord_queue_processor import (
    QueueAttempt,
    QueueAttemptNeedsReviewError,
    QueueGenerationRecovery,
    QueueJobSummary,
    QueueTurnCoordinatorDeps,
    QueueTurnOwnershipAmbiguousError,
)
from codex_discord_runner_queue import QueueJob, QueueJobValue
import codex_discord_store as store
from codex_discord_store_queue import QueueJobState, StoredQueueJob
from codex_discord_store_attempts import QueueAttemptState, StoredQueueAttempt
from codex_discord_store_inbox import DeferredInboxRecord, DeferredInboxState


TurnStateGetter = Callable[[str, int], dict[str, TurnCompletion]]
LiveTurnWaiter = Callable[[str, str, float, int], TurnCompletionObservation]
PromptSender = Callable[..., Awaitable[prompt_delivery_prepare.PromptDeliveryPreparationResult]]
ChunkSender = Callable[..., Awaitable[int]]
QueueAdmission = Callable[
    [int | None],
    AbstractContextManager[AppServerLifecycleSnapshot],
]


class QueueTurnDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeferredIntakeResult:
    inbox: DeferredInboxRecord
    jobs: tuple[StoredQueueJob, ...]
    pending: bool


@dataclass(frozen=True, slots=True)
class DeferredReplaySeed:
    target_thread_id: str
    channel: QueueJobValue


@dataclass(frozen=True, slots=True)
class DeferredInboxRestore:
    jobs: tuple[QueueJob, ...]
    seeds: tuple[DeferredReplaySeed, ...]


@dataclass(frozen=True, slots=True)
class DurableQueueRuntimeDeps:
    get_db_path: Callable[[], Path]
    get_app_server_lifecycle: queue_generation.LifecycleSnapshotGetter
    ensure_app_server_ready: Callable[[], None]
    get_expected_app_server_generation: Callable[[], int | None]
    admit_app_server_generation: QueueAdmission
    notify_app_server_work_changed: Callable[[], None]
    notify_late_queue_attempt_reconciled: Callable[
        [store.LateQueueAttemptReconciliation, QueueJob, asyncio.AbstractEventLoop],
        None,
    ]
    get_turn_states: TurnStateGetter
    wait_for_live_turn: LiveTurnWaiter
    run_prompt_and_send: PromptSender
    send_chunks: ChunkSender
    log: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class DurableQueueRuntime:
    deps: DurableQueueRuntimeDeps

    async def intake_deferred(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str,
        *,
        source_message: QueueJobValue,
    ) -> DeferredIntakeResult | None:
        channel_id = int(getattr(channel, "id", 0) or 0)
        message_id = int(getattr(source_message, "id", 0) or 0)
        author = getattr(source_message, "author", None)
        owner_user_id = int(getattr(author, "id", 0) or 0) or None
        if channel_id <= 0 or message_id <= 0 or not target_thread_id:
            return None
        claim = await asyncio.to_thread(
            store.claim_deferred_discord_message,
            self.deps.get_db_path(),
            message_id=message_id,
            target_thread_id=target_thread_id,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
            prompt=prompt,
            source="gateway",
            normalization_version=1,
        )
        return DeferredIntakeResult(
            claim.record,
            (),
            claim.record.state is DeferredInboxState.RECEIVED,
        )

    async def promote_deferred_target(
        self,
        target_thread_id: str,
        *,
        channel_id: int,
    ) -> tuple[StoredQueueJob, ...]:
        snapshot = self.deps.get_app_server_lifecycle()
        if not snapshot.healthy or snapshot.generation <= 0:
            return ()
        promotion = await asyncio.to_thread(
            store.promote_deferred_discord_messages,
            self.deps.get_db_path(),
            target_thread_id=target_thread_id,
            channel_id=channel_id,
            app_server_generation=snapshot.generation,
            lease_owner=str(uuid.uuid4()),
        )
        current = self.deps.get_app_server_lifecycle()
        if not current.healthy or current.generation != snapshot.generation:
            self.deps.log(
                "deferred_promotion_generation_changed "
                + f"target={target_thread_id} channel={channel_id} "
                + f"committed={snapshot.generation} "
                + f"current={current.generation if current.healthy else 'unhealthy'}"
            )
        if promotion.jobs:
            self.deps.notify_app_server_work_changed()
        return promotion.jobs

    async def recover_generation_expired_jobs(
        self,
        candidates: tuple[QueueJob, ...],
    ) -> QueueGenerationRecovery:
        candidates_by_id = {
            job_id: job
            for job in candidates
            if (job_id := str(job.get("job_id") or "").strip())
        }
        if not candidates_by_id:
            return QueueGenerationRecovery((), ())
        while True:
            snapshot = self.deps.get_app_server_lifecycle()
            if not snapshot.healthy or snapshot.generation <= 0:
                try:
                    await asyncio.to_thread(self.deps.ensure_app_server_ready)
                except Exception as exc:  # noqa: BLE001 - retain jobs and surface every resident startup failure.
                    self.deps.log(
                        "queue_generation_recovery_waiting "
                        + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                    )
                    await asyncio.sleep(1.0)
                    continue
                snapshot = self.deps.get_app_server_lifecycle()
            if not snapshot.healthy or snapshot.generation <= 0:
                self.deps.log("queue_generation_recovery_waiting reason=app_server_unhealthy")
                await asyncio.sleep(1.0)
                continue

            reconciliation = await asyncio.to_thread(
                store.reconcile_queue_jobs_for_generation,
                self.deps.get_db_path(),
                snapshot.generation,
            )
            records = await asyncio.to_thread(
                store.list_queue_jobs,
                self.deps.get_db_path(),
                None,
                app_server_generation=snapshot.generation,
            )
            current = self.deps.get_app_server_lifecycle()
            if not current.healthy or current.generation != snapshot.generation:
                self.deps.log(
                    "queue_generation_recovery_retry "
                    + f"expected={snapshot.generation} "
                    + f"current={current.generation if current.healthy else 'unhealthy'}"
                )
                await asyncio.sleep(0)
                continue

            recovered: list[QueueJob] = []
            for record in records:
                candidate = candidates_by_id.get(record.job_id)
                if candidate is None:
                    continue
                recovered.append(
                    to_memory_queue_job(
                        record,
                        candidate.get("channel"),
                        candidate.get("source_message"),
                    )
                )
            needs_review = tuple(
                job_id
                for job_id in reconciliation.needs_review_job_ids
                if job_id in candidates_by_id
            )
            self.deps.notify_app_server_work_changed()
            return QueueGenerationRecovery(tuple(recovered), needs_review)

    async def reconcile_deferred_target(
        self,
        target_thread_id: str,
        *,
        channel_id: int,
    ) -> tuple[StoredQueueJob, ...]:
        snapshot = self.deps.get_app_server_lifecycle()
        if not snapshot.healthy or snapshot.generation <= 0:
            return ()
        reconciliation = await asyncio.to_thread(
            store.reconcile_queue_jobs_for_generation,
            self.deps.get_db_path(),
            snapshot.generation,
        )
        if reconciliation.needs_review_job_ids:
            self.deps.log(
                "deferred_reconcile_needs_review "
                + f"target={target_thread_id} channel={channel_id} "
                + f"count={len(reconciliation.needs_review_job_ids)} "
                + f"jobs={','.join(reconciliation.needs_review_job_ids[:5])}"
            )
        records = await asyncio.to_thread(
            store.list_executable_queue_jobs,
            self.deps.get_db_path(),
            target_thread_id=target_thread_id,
            channel_id=channel_id,
            app_server_generation=snapshot.generation,
        )
        current = self.deps.get_app_server_lifecycle()
        if not current.healthy or current.generation != snapshot.generation:
            return ()
        return tuple(records)

    async def restore_deferred_inbox(
        self,
        bot: QueueRestoreBot,
    ) -> DeferredInboxRestore:
        restored: list[QueueJob] = []
        seeds: list[DeferredReplaySeed] = []
        pending = await asyncio.to_thread(
            store.list_deferred_discord_messages,
            self.deps.get_db_path(),
            state=DeferredInboxState.RECEIVED,
        )
        groups: dict[tuple[str, int], DeferredInboxRecord] = {}
        for record in pending:
            groups.setdefault((record.target_thread_id, record.channel_id), record)
        for first in groups.values():
            channel, source = bot.get_cached_channel_or_thread(first.channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(first.channel_id)
                    source = "fetch"
                except (OSError, RuntimeError, TimeoutError) as exc:
                    self.deps.log(
                        f"deferred_inbox_channel_failed channel={first.channel_id} "
                        + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                    )
                    continue
            if not bot.is_allowed_message_channel(channel):
                self.deps.log(
                    f"deferred_inbox_channel_denied channel={first.channel_id} source={source}"
                )
                continue
            seeds.append(DeferredReplaySeed(first.target_thread_id, channel))
            promoted = await self.promote_deferred_target(
                first.target_thread_id,
                channel_id=first.channel_id,
            )
            restored.extend(
                to_memory_queue_job(record, channel, None)
                for record in promoted
            )
        return DeferredInboxRestore(tuple(restored), tuple(seeds))

    async def enqueue(
        self,
        channel: QueueJobValue,
        prompt: str,
        target_thread_id: str,
        *,
        queued: bool,
        ack_sent: bool,
        source_message: QueueJobValue,
    ) -> tuple[QueueJob, bool, int]:
        channel_id = int(getattr(channel, "id", 0) or 0)
        author = getattr(source_message, "author", None)
        owner_user_id = int(getattr(author, "id", 0) or 0) or None
        discord_message_id = int(getattr(source_message, "id", 0) or 0) or None
        if channel_id <= 0:
            raise QueueTurnDeliveryError("Durable queue delivery requires a Discord channel id.")
        expected_generation = self.deps.get_expected_app_server_generation()
        with self.deps.admit_app_server_generation(expected_generation) as snapshot:
            generation = int(snapshot.generation if expected_generation is None else expected_generation)
            if (
                not snapshot.healthy
                or generation <= 0
                or int(snapshot.generation) != generation
            ):
                await queue_generation.require_queue_generation(
                    self.deps,
                    generation,
                    "enqueue_admission",
                )
            _ = await queue_generation.discard_stale_queue_jobs(self.deps, snapshot)
            queue_generation.reject_source_before_accepting_since(
                self.deps,
                source_message,
                snapshot,
                channel_id=channel_id,
            )
            await queue_generation.require_queue_generation(
                self.deps,
                generation,
                "enqueue_before_store",
            )
            result = await asyncio.to_thread(
                store.enqueue_queue_job,
                self.deps.get_db_path(),
                job_id=str(uuid.uuid4()),
                target_thread_id=target_thread_id,
                channel_id=channel_id,
                owner_user_id=owner_user_id,
                discord_message_id=discord_message_id,
                app_server_generation=generation,
                prompt=prompt,
                queued=queued,
                ack_sent=ack_sent,
            )
            await queue_generation.require_queue_generation(
                self.deps,
                generation,
                "enqueue_after_store",
            )
            records = await asyncio.to_thread(
                store.list_queue_jobs,
                self.deps.get_db_path(),
                target_thread_id,
                app_server_generation=generation,
            )
            return to_memory_queue_job(result.job, channel, source_message), result.created, len(records)

    async def acquire_turn(
        self,
        job: QueueJob,
        prompt: str,
        target_thread_id: str | None,
        *,
        recovery: bool,
    ) -> QueueAttempt:
        job_id = str(job.get("job_id") or "").strip()
        thread_id = str(target_thread_id or job.get("target_thread_id") or "").strip()
        if not job_id or not thread_id:
            raise QueueTurnDeliveryError("Durable queue job is missing its job or thread id.")
        generation = queue_job_int(job.get("app_server_generation"))
        await queue_generation.require_queue_generation(self.deps, generation, "acquire")
        if not recovery:
            restored = await self._reconcile_restored_attempt(job, thread_id, generation)
            if restored is not None:
                return restored
        states = await self._wait_for_startable_turn(thread_id, generation)
        owning_loop = asyncio.get_running_loop()
        attempt_record = await asyncio.to_thread(
            store.begin_queue_execution_attempt,
            self.deps.get_db_path(),
            job_id,
            baseline_turn_ids=tuple(states),
            app_server_generation=generation,
        )
        job["state"] = QueueJobState.STARTING.value
        job["attempt_count"] = attempt_record.attempt_number
        job["turn_id"] = None
        job["baseline_turn_ids"] = tuple(states)
        attempt_count = attempt_record.attempt_number
        channel = job.get("channel")
        if channel is None or not hasattr(channel, "send"):
            raise QueueTurnDeliveryError("Durable queue job has no send-capable Discord channel.")
        await queue_generation.require_queue_generation(self.deps, generation, "turn_start")

        def mark_prewrite(request_id: str, process_id: int, request_generation: int) -> None:
            if request_generation != generation:
                raise AppServerGenerationMismatch(
                    expected_generation=generation,
                    actual_generation=request_generation,
                    healthy=False,
                )
            _ = store.mark_queue_attempt_prewrite(
                self.deps.get_db_path(),
                attempt_record.attempt_id,
                client_request_id=request_id,
                app_server_process_id=process_id,
            )

        def mark_write_crossed(
            request_id: str,
            process_id: int,
            request_generation: int,
        ) -> None:
            _ = request_id, process_id
            if request_generation != generation:
                raise AppServerGenerationMismatch(
                    expected_generation=generation,
                    actual_generation=request_generation,
                    healthy=False,
                )
            _ = store.mark_queue_attempt_write_crossed(
                self.deps.get_db_path(),
                attempt_record.attempt_id,
            )

        def reconcile_late_success(
            request_id: str,
            process_id: int,
            request_generation: int,
            response_thread_id: str,
            turn_id: str,
        ) -> None:
            reconciliation = store.reconcile_late_queue_attempt_running(
                self.deps.get_db_path(),
                client_request_id=request_id,
                app_server_process_id=process_id,
                app_server_generation=request_generation,
                target_thread_id=response_thread_id,
                turn_id=turn_id,
            )
            if reconciliation is not None:
                self.deps.notify_app_server_work_changed()
                self.deps.notify_late_queue_attempt_reconciled(
                    reconciliation,
                    job,
                    owning_loop,
                )
                self.deps.log(
                    "queue_turn_late_start_reconciled "
                    + f"job={reconciliation.job_id} generation={request_generation} turn={turn_id}"
                )

        try:
            with bind_turn_start_attempt(
                before_write=mark_prewrite,
                after_write=mark_write_crossed,
                late_success=reconcile_late_success,
            ):
                preparation = await self.deps.run_prompt_and_send(
                    channel,
                    prompt,
                    queued=bool(job.get("queued")),
                    ack_sent=bool(job.get("ack_sent")),
                    source_message=job.get("source_message"),
                    target_thread_id=thread_id,
                    expected_app_server_generation=generation,
                )
        except AppServerGenerationMismatch as exc:
            await queue_generation.translate_transport_generation_expiry(
                self.deps,
                exc,
                generation,
                "turn_start",
                job_id=job_id,
            )
        except Exception as exc:
            reconciled = await self._resolve_attempt_after_failure(attempt_record, exc)
            if reconciled is not None:
                return reconciled
            raise
        mapped = preparation.mapped_result
        if mapped is None or not mapped.accepted or not mapped.turn_id:
            detail = mapped.error_message if mapped is not None else "mapped app-server delivery was not used"
            reconciled = await self._resolve_attempt_after_failure(
                attempt_record,
                QueueTurnDeliveryError(f"Queue turn was not accepted: {detail[:500]}"),
            )
            if reconciled is not None:
                return reconciled
            raise QueueTurnDeliveryError(f"Queue turn was not accepted: {detail[:500]}")
        latest = await asyncio.to_thread(
            store.get_latest_queue_execution_attempt,
            self.deps.get_db_path(),
            job_id,
        )
        if latest is None or latest.state is not QueueAttemptState.START_UNKNOWN:
            if latest is not None and latest.state is not QueueAttemptState.NEEDS_REVIEW:
                _ = await asyncio.to_thread(
                    store.mark_queue_attempt_needs_review,
                    self.deps.get_db_path(),
                    latest.attempt_id,
                    last_error="turn accepted without the exact resident write boundary",
                )
            raise QueueAttemptNeedsReviewError(
                f"Queue turn/start write boundary was not recorded for job {job_id}."
            )
        await queue_generation.require_queue_generation(
            self.deps,
            generation,
            "turn_accepted",
        )
        _ = await asyncio.to_thread(
            store.mark_queue_execution_running,
            self.deps.get_db_path(),
            job_id=job_id,
            attempt_id=attempt_record.attempt_id,
            app_server_generation=generation,
            turn_id=mapped.turn_id,
        )
        record = await asyncio.to_thread(
            store.list_queue_jobs,
            self.deps.get_db_path(),
            thread_id,
            app_server_generation=generation,
        )
        accepted_record = next(
            (item for item in record if item.job_id == job_id),
            None,
        )
        if accepted_record is None:
            raise QueueTurnDeliveryError(
                f"Queue job {job_id} disappeared after its atomic running transition."
            )
        copy_stored_queue_state(job, accepted_record)
        return QueueAttempt(
            attempt_count,
            thread_id,
            mapped.turn_id,
            generation,
            attempt_record.attempt_id,
        )

    async def _resolve_attempt_after_failure(
        self,
        attempt: StoredQueueAttempt,
        exc: Exception,
    ) -> QueueAttempt | None:
        latest = await asyncio.to_thread(
            store.resolve_queue_attempt_failure,
            self.deps.get_db_path(),
            attempt.attempt_id,
            last_error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )
        if latest.state is QueueAttemptState.EXEC_PENDING:
            return None
        if latest.state is QueueAttemptState.RUNNING and latest.turn_id:
            self.deps.log(
                "queue_turn_late_start_rejoined "
                + f"job={latest.job_id} generation={latest.app_server_generation} "
                + f"turn={latest.turn_id}"
            )
            return QueueAttempt(
                latest.attempt_number,
                latest.target_thread_id,
                latest.turn_id,
                latest.app_server_generation,
                latest.attempt_id,
            )
        raise QueueAttemptNeedsReviewError(
            f"Queue turn/start crossed the write boundary; job {attempt.job_id} requires review."
        ) from exc

    async def _wait_for_startable_turn(
        self,
        thread_id: str,
        generation: int,
    ) -> dict[str, TurnCompletion]:
        waiting_logged = False
        while True:
            await queue_generation.require_queue_generation(
                self.deps,
                generation,
                "wait_for_startable_turn",
            )
            try:
                states = await asyncio.to_thread(
                    self.deps.get_turn_states,
                    thread_id,
                    generation,
                )
            except AppServerGenerationMismatch as exc:
                await queue_generation.translate_transport_generation_expiry(
                    self.deps,
                    exc,
                    generation,
                    "wait_for_startable_turn",
                )
            active_turn_ids = [
                turn_id
                for turn_id, completion in states.items()
                if completion.status is TurnStatus.IN_PROGRESS
            ]
            if not active_turn_ids:
                if waiting_logged:
                    self.deps.log(f"queue_active_turn_cleared target={thread_id}")
                return states
            if not waiting_logged:
                self.deps.log(
                    f"queue_waiting_for_active_turn target={thread_id} "
                    + f"turns={','.join(active_turn_ids[:3])}"
                )
                waiting_logged = True
            await asyncio.sleep(1.0)

    async def wait_for_turn_completion(
        self,
        thread_id: str,
        turn_id: str,
        generation: int,
    ) -> TurnCompletion:
        while True:
            await queue_generation.require_queue_generation(
                self.deps,
                generation,
                "wait_for_turn_completion",
            )
            try:
                observation = await asyncio.to_thread(
                    self.deps.wait_for_live_turn,
                    thread_id,
                    turn_id,
                    2.0,
                    generation,
                )
            except AppServerGenerationMismatch as exc:
                await queue_generation.translate_transport_generation_expiry(
                    self.deps,
                    exc,
                    generation,
                    "wait_for_turn_completion",
                )
            await queue_generation.require_queue_generation(
                self.deps,
                generation,
                "wait_for_turn_completion",
            )
            if isinstance(observation, TurnCompletionFound):
                return observation.completion
            if isinstance(observation, TurnCompletionTransportError):
                self.deps.log(
                    f"queue_turn_live_wait_transport_error target={thread_id} turn={turn_id} "
                    + f"error={observation.message[:300]}"
                )
            try:
                states = await asyncio.to_thread(
                    self.deps.get_turn_states,
                    thread_id,
                    generation,
                )
            except AppServerGenerationMismatch as exc:
                await queue_generation.translate_transport_generation_expiry(
                    self.deps,
                    exc,
                    generation,
                    "reconcile_turn_completion",
                )
            except (OSError, RuntimeError, TimeoutError) as exc:
                self.deps.log(
                    f"queue_turn_reconcile_retry target={thread_id} turn={turn_id} "
                    + f"error_type={type(exc).__name__} error={str(exc)[:300]}"
                )
                await asyncio.sleep(2.0)
                continue
            state = states.get(turn_id)
            if state is not None and state.status is not TurnStatus.IN_PROGRESS:
                return state
            await asyncio.sleep(1.0)

    async def complete_job(self, job: QueueJob) -> None:
        job_id = str(job.get("job_id") or "")
        generation = queue_job_int(job.get("app_server_generation"))
        await queue_generation.require_queue_generation(
            self.deps,
            generation,
            "complete_job",
        )
        latest = await asyncio.to_thread(
            store.get_latest_queue_execution_attempt,
            self.deps.get_db_path(),
            job_id,
        )
        if latest is None or latest.state is not QueueAttemptState.RUNNING:
            raise QueueTurnDeliveryError(
                f"Queue job {job_id} has no running execution attempt to complete."
            )
        await asyncio.to_thread(
            store.complete_queue_execution_attempt,
            self.deps.get_db_path(),
            job_id=job_id,
            attempt_id=latest.attempt_id,
        )
        self.deps.notify_app_server_work_changed()

    async def flush_jobs(self, job: QueueJob, target_thread_id: str | None) -> list[QueueJobSummary]:
        generation = queue_job_int(job.get("app_server_generation"))
        await queue_generation.require_queue_generation(self.deps, generation, "flush_jobs")
        thread_id = str(target_thread_id or job.get("target_thread_id") or "")
        records = await asyncio.to_thread(
            store.flush_queue_jobs,
            self.deps.get_db_path(),
            thread_id,
            app_server_generation=generation,
        )
        self.deps.notify_app_server_work_changed()
        return [QueueJobSummary(record.job_id, record.prompt) for record in records]

    async def report_retry(self, job: QueueJob, reason: str) -> None:
        generation = queue_job_int(job.get("app_server_generation"))
        await queue_generation.require_queue_generation(self.deps, generation, "report_retry")
        channel = job.get("channel")
        if channel is not None and hasattr(channel, "send"):
            _ = await self.deps.send_chunks(
                channel,
                build_queue_retry_message(reason),
                context="queue_turn_retry",
            )

    async def report_batch_failure(
        self,
        job: QueueJob,
        reason: str,
        deleted_jobs: list[QueueJobSummary],
    ) -> None:
        channel = job.get("channel")
        if channel is None or not hasattr(channel, "send"):
            return
        _ = await self.deps.send_chunks(
            channel,
            build_queue_batch_failure_message(reason, deleted_jobs),
            context="queue_batch_flushed",
        )

    def coordinator_deps(self) -> QueueTurnCoordinatorDeps:
        return QueueTurnCoordinatorDeps(
            acquire_turn=self.acquire_turn,
            wait_for_turn_completion=self.wait_for_turn_completion,
            complete_job=self.complete_job,
            flush_jobs=self.flush_jobs,
            report_retry=self.report_retry,
            report_batch_failure=self.report_batch_failure,
            log=self.deps.log,
        )

    async def restore_jobs(self, bot: QueueRestoreBot) -> list[QueueJob]:
        return await restore_queue_jobs(bot, self.deps)

    async def retract_job(
        self,
        target_thread_id: str,
        *,
        channel_id: int | None,
        owner_user_id: int | None,
    ) -> StoredQueueJob | None:
        record = await asyncio.to_thread(
            store.retract_queue_job,
            self.deps.get_db_path(),
            target_thread_id,
            channel_id=channel_id,
            owner_user_id=owner_user_id,
        )
        if record is not None:
            self.deps.notify_app_server_work_changed()
        return record

    async def _reconcile_restored_attempt(
        self,
        job: QueueJob,
        thread_id: str,
        generation: int,
    ) -> QueueAttempt | None:
        state = str(job.get("state") or QueueJobState.PENDING.value)
        if state == QueueJobState.PENDING.value:
            return None
        attempt = await asyncio.to_thread(
            store.get_latest_queue_execution_attempt,
            self.deps.get_db_path(),
            str(job.get("job_id") or ""),
        )
        if attempt is not None and attempt.state is QueueAttemptState.EXEC_PENDING:
            return None
        if (
            attempt is not None
            and attempt.state is QueueAttemptState.RUNNING
            and attempt.turn_id
        ):
            return QueueAttempt(
                attempt.attempt_number,
                thread_id,
                attempt.turn_id,
                generation,
                attempt.attempt_id,
            )
        if attempt is not None and attempt.state is not QueueAttemptState.NEEDS_REVIEW:
            _ = await asyncio.to_thread(
                store.mark_queue_attempt_needs_review,
                self.deps.get_db_path(),
                attempt.attempt_id,
                last_error="restored after an unresolved turn/start write boundary",
            )
        raise QueueAttemptNeedsReviewError(
            f"Queue job {job.get('job_id') or '-'} cannot be replayed safely after restart."
        )
