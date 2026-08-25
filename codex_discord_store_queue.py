from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
import json
from pathlib import Path
import sqlite3
import time
from collections.abc import Callable
from typing import TypeAlias, cast

from codex_discord_store_connection import connect_store
from codex_discord_store_schema import init_store_schema


SQLiteCell: TypeAlias = str | int | float | bytes | None
SQLiteRow: TypeAlias = tuple[SQLiteCell, ...]
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
DecodeJsonValue: TypeAlias = Callable[[str], JsonValue]
_decode_json_value: DecodeJsonValue = json.loads

QUEUE_JOB_COLUMNS = (
    "job_id, target_thread_id, channel_id, owner_user_id, discord_message_id, "
    "app_server_generation, prompt, queued, ack_sent, state, attempt_count, turn_id, "
    "baseline_turn_ids, last_error, created_at, updated_at"
)


@unique
class QueueJobState(StrEnum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class StoredQueueJob:
    job_id: str
    target_thread_id: str
    channel_id: int
    owner_user_id: int | None
    discord_message_id: int | None
    app_server_generation: int
    prompt: str
    queued: bool
    ack_sent: bool
    state: QueueJobState
    attempt_count: int
    turn_id: str | None
    baseline_turn_ids: tuple[str, ...]
    last_error: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class QueueEnqueueResult:
    job: StoredQueueJob
    created: bool


@dataclass(frozen=True, slots=True)
class QueueGenerationAdoption:
    jobs: tuple[StoredQueueJob, ...]
    adopted_count: int
    needs_review_job_ids: tuple[str, ...]


class QueueJobNotFoundError(LookupError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Durable queue job not found: {job_id}")
        self.job_id: str = job_id


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = connect_store(db_path)
    init_store_schema(conn)
    return conn


def _record(row: SQLiteRow) -> StoredQueueJob:
    baseline = _decode_baseline(str(row[12] or "[]"))
    return StoredQueueJob(
        job_id=str(row[0]),
        target_thread_id=str(row[1]),
        channel_id=int(cast(int, row[2])),
        owner_user_id=int(cast(int, row[3])) if row[3] is not None else None,
        discord_message_id=int(cast(int, row[4])) if row[4] is not None else None,
        app_server_generation=int(cast(int, row[5])),
        prompt=str(row[6]),
        queued=bool(row[7]),
        ack_sent=bool(row[8]),
        state=QueueJobState(str(row[9])),
        attempt_count=int(cast(int, row[10])),
        turn_id=str(row[11]) if row[11] is not None else None,
        baseline_turn_ids=baseline,
        last_error=str(row[13] or ""),
        created_at=float(cast(float, row[14])),
        updated_at=float(cast(float, row[15])),
    )


def _decode_baseline(raw: str) -> tuple[str, ...]:
    decoded = _decode_json_value(raw)
    if not isinstance(decoded, list):
        return ()
    return tuple(str(value) for value in decoded)


def _select_job(conn: sqlite3.Connection, job_id: str) -> StoredQueueJob:
    row = cast(
        SQLiteRow | None,
        conn.execute(
            f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue WHERE job_id = ?",
            (job_id,),
        ).fetchone(),
    )
    if row is None:
        raise QueueJobNotFoundError(job_id)
    return _record(row)


def enqueue_queue_job(
    db_path: Path,
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
    now = time.time() if created_at is None else created_at
    with _connect(db_path) as conn:
        result = conn.execute(
            "INSERT OR IGNORE INTO codex_turn_queue "
            + "(job_id, target_thread_id, channel_id, owner_user_id, discord_message_id, "
            + "app_server_generation, prompt, "
            + "queued, ack_sent, state, attempt_count, baseline_turn_ids, created_at, updated_at) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '[]', ?, ?)",
            (
                job_id,
                target_thread_id,
                channel_id,
                owner_user_id,
                discord_message_id,
                app_server_generation,
                prompt,
                int(queued),
                int(ack_sent),
                QueueJobState.PENDING.value,
                now,
                now,
            ),
        )
        created = result.rowcount == 1
        if created:
            job = _select_job(conn, job_id)
        elif discord_message_id is not None:
            row = cast(
                SQLiteRow | None,
                conn.execute(
                    f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue WHERE discord_message_id = ?",
                    (discord_message_id,),
                ).fetchone(),
            )
            if row is None:
                raise QueueJobNotFoundError(job_id)
            job = _record(row)
        else:
            job = _select_job(conn, job_id)
    return QueueEnqueueResult(job, created)


def list_queue_jobs(
    db_path: Path,
    target_thread_id: str | None = None,
    *,
    app_server_generation: int | None = None,
) -> list[StoredQueueJob]:
    with _connect(db_path) as conn:
        clauses: list[str] = []
        params: list[SQLiteCell] = []
        if target_thread_id is not None:
            clauses.append("target_thread_id = ?")
            params.append(target_thread_id)
        if app_server_generation is not None:
            clauses.append("app_server_generation = ?")
            params.append(app_server_generation)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue{where} "
                "ORDER BY created_at, job_id",
                params,
            ).fetchall(),
        )
    return [_record(row) for row in rows]


def list_executable_queue_jobs(
    db_path: Path,
    *,
    target_thread_id: str,
    channel_id: int,
    app_server_generation: int,
) -> list[StoredQueueJob]:
    with _connect(db_path) as conn:
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {', '.join(f'q.{column.strip()}' for column in QUEUE_JOB_COLUMNS.split(','))} "
                "FROM codex_turn_queue q LEFT JOIN codex_turn_attempts a ON a.attempt_id = ("
                "SELECT latest.attempt_id FROM codex_turn_attempts latest "
                "WHERE latest.job_id = q.job_id ORDER BY latest.attempt_number DESC LIMIT 1) "
                "WHERE q.target_thread_id = ? AND q.channel_id = ? "
                "AND q.app_server_generation = ? "
                "AND (a.attempt_id IS NULL OR a.state IN ('exec_pending', 'running')) "
                "ORDER BY q.created_at, q.job_id",
                (target_thread_id, channel_id, app_server_generation),
            ).fetchall(),
        )
    return [_record(row) for row in rows]


def has_queue_jobs_for_target_channel(
    db_path: Path,
    *,
    target_thread_id: str,
    channel_id: int,
    app_server_generation: int | None = None,
) -> bool:
    clauses = ["target_thread_id = ?", "channel_id = ?"]
    params: list[SQLiteCell] = [target_thread_id, channel_id]
    if app_server_generation is not None:
        clauses.append("app_server_generation = ?")
        params.append(app_server_generation)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT 1 FROM codex_turn_queue WHERE {' AND '.join(clauses)} LIMIT 1",
            params,
        ).fetchone()
    return row is not None


def has_executable_queue_jobs_for_target_channel(
    db_path: Path,
    *,
    target_thread_id: str,
    channel_id: int,
    app_server_generation: int | None = None,
) -> bool:
    clauses = ["q.target_thread_id = ?", "q.channel_id = ?"]
    params: list[SQLiteCell] = [target_thread_id, channel_id]
    if app_server_generation is not None:
        clauses.append("q.app_server_generation = ?")
        params.append(app_server_generation)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM codex_turn_queue q LEFT JOIN codex_turn_attempts a "
            "ON a.attempt_id = (SELECT latest.attempt_id FROM codex_turn_attempts latest "
            "WHERE latest.job_id = q.job_id ORDER BY latest.attempt_number DESC LIMIT 1) "
            f"WHERE {' AND '.join(clauses)} "
            "AND (a.attempt_id IS NULL OR a.state IN ('exec_pending', 'running')) LIMIT 1",
            params,
        ).fetchone()
    return row is not None


def has_executable_queue_work(db_path: Path) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM codex_turn_queue q LEFT JOIN codex_turn_attempts a "
            "ON a.attempt_id = (SELECT latest.attempt_id FROM codex_turn_attempts latest "
            "WHERE latest.job_id = q.job_id ORDER BY latest.attempt_number DESC LIMIT 1) "
            "WHERE a.attempt_id IS NULL OR a.state != 'needs_review' LIMIT 1"
        ).fetchone()
    return row is not None


def adopt_queue_jobs_generation(
    db_path: Path,
    app_server_generation: int,
) -> QueueGenerationAdoption:
    from codex_discord_store_attempts import reconcile_queue_jobs_for_generation

    reconciliation = reconcile_queue_jobs_for_generation(
        db_path,
        app_server_generation,
    )
    with _connect(db_path) as conn:
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue "
                "WHERE app_server_generation = ? "
                "ORDER BY created_at, job_id",
                (app_server_generation,),
            ).fetchall(),
        )
    return QueueGenerationAdoption(
        jobs=tuple(_record(row) for row in rows),
        adopted_count=len(reconciliation.adopted_job_ids),
        needs_review_job_ids=reconciliation.needs_review_job_ids,
    )


def discard_queue_jobs_for_generation(
    db_path: Path,
    app_server_generation: int | None,
) -> list[StoredQueueJob]:
    """Atomically remove jobs that cannot run in the supplied server generation.

    ``None`` means that the app server is unhealthy, so every queued job is stale.
    """
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        if app_server_generation is None:
            where = ""
            params: tuple[int, ...] = ()
        else:
            where = " WHERE app_server_generation != ?"
            params = (app_server_generation,)
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue{where} "
                "ORDER BY created_at, job_id",
                params,
            ).fetchall(),
        )
        _ = conn.execute(f"DELETE FROM codex_turn_queue{where}", params)
    return [_record(row) for row in rows]


def discard_observed_queue_jobs(
    db_path: Path,
    observed_jobs: list[StoredQueueJob] | tuple[StoredQueueJob, ...],
) -> list[StoredQueueJob]:
    """Atomically remove rows only while their observed generation still matches."""
    ids_by_generation: dict[int, dict[str, None]] = {}
    for job in observed_jobs:
        ids_by_generation.setdefault(job.app_server_generation, {})[job.job_id] = None
    if not ids_by_generation:
        return []
    rows: list[SQLiteRow] = []
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        for generation, generation_ids in ids_by_generation.items():
            unique_ids = tuple(generation_ids)
            for offset in range(0, len(unique_ids), 499):
                chunk = unique_ids[offset : offset + 499]
                placeholders = ",".join("?" for _ in chunk)
                params: tuple[SQLiteCell, ...] = (*chunk, generation)
                where = (
                    f"job_id IN ({placeholders}) AND app_server_generation = ?"
                )
                rows.extend(
                    cast(
                        list[SQLiteRow],
                        conn.execute(
                            f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue WHERE {where}",
                            params,
                        ).fetchall(),
                    )
                )
                _ = conn.execute(
                    f"DELETE FROM codex_turn_queue WHERE {where}",
                    params,
                )
    records = [_record(row) for row in rows]
    return sorted(records, key=lambda record: (record.created_at, record.job_id))


def begin_queue_job_attempt(
    db_path: Path,
    job_id: str,
    *,
    baseline_turn_ids: tuple[str, ...],
    app_server_generation: int,
) -> StoredQueueJob:
    with _connect(db_path) as conn:
        now = time.time()
        result = conn.execute(
            "UPDATE codex_turn_queue SET state = ?, attempt_count = attempt_count + 1, "
            + "turn_id = NULL, baseline_turn_ids = ?, last_error = '', updated_at = ? "
            + "WHERE job_id = ? AND app_server_generation = ?",
            (
                QueueJobState.STARTING.value,
                json.dumps(baseline_turn_ids),
                now,
                job_id,
                app_server_generation,
            ),
        )
        if result.rowcount != 1:
            raise QueueJobNotFoundError(job_id)
        return _select_job(conn, job_id)


def mark_queue_job_running(
    db_path: Path,
    job_id: str,
    turn_id: str,
    *,
    app_server_generation: int,
) -> StoredQueueJob:
    with _connect(db_path) as conn:
        result = conn.execute(
            "UPDATE codex_turn_queue SET state = ?, turn_id = ?, updated_at = ? "
            "WHERE job_id = ? AND app_server_generation = ?",
            (
                QueueJobState.RUNNING.value,
                turn_id,
                time.time(),
                job_id,
                app_server_generation,
            ),
        )
        if result.rowcount != 1:
            raise QueueJobNotFoundError(job_id)
        return _select_job(conn, job_id)


def complete_queue_job(db_path: Path, job_id: str) -> bool:
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'completed', updated_at = ? "
            "WHERE queue_job_id = ? AND state = 'promoted'",
            (time.time(), job_id),
        )
        result = conn.execute("DELETE FROM codex_turn_queue WHERE job_id = ?", (job_id,))
        return result.rowcount == 1


def flush_queue_jobs(
    db_path: Path,
    target_thread_id: str,
    *,
    app_server_generation: int,
) -> list[StoredQueueJob]:
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue "
                "WHERE target_thread_id = ? AND app_server_generation = ? "
                "ORDER BY created_at, job_id",
                (target_thread_id, app_server_generation),
            ).fetchall(),
        )
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'failed', updated_at = ? "
            "WHERE state = 'promoted' AND queue_job_id IN ("
            "SELECT job_id FROM codex_turn_queue "
            "WHERE target_thread_id = ? AND app_server_generation = ?)",
            (time.time(), target_thread_id, app_server_generation),
        )
        _ = conn.execute(
            "DELETE FROM codex_turn_queue "
            "WHERE target_thread_id = ? AND app_server_generation = ?",
            (target_thread_id, app_server_generation),
        )
    return [_record(row) for row in rows]


def retract_queue_job(
    db_path: Path,
    target_thread_id: str,
    *,
    channel_id: int | None,
    owner_user_id: int | None,
) -> StoredQueueJob | None:
    clauses = ["target_thread_id = ?", "state = ?"]
    params: list[SQLiteCell] = [target_thread_id, QueueJobState.PENDING.value]
    if channel_id is not None:
        clauses.append("channel_id = ?")
        params.append(channel_id)
    if owner_user_id is not None:
        clauses.append("owner_user_id = ?")
        params.append(owner_user_id)
    where = " AND ".join(clauses)
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        row = cast(
            SQLiteRow | None,
            conn.execute(
                f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue WHERE {where} "
                "ORDER BY created_at DESC, job_id DESC LIMIT 1",
                params,
            ).fetchone(),
        )
        if row is None:
            return None
        job = _record(row)
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'cancelled', updated_at = ? "
            "WHERE queue_job_id = ? AND state = 'promoted'",
            (time.time(), job.job_id),
        )
        _ = conn.execute("DELETE FROM codex_turn_queue WHERE job_id = ?", (job.job_id,))
        return job
