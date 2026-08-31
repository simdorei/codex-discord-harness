from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
import json
from pathlib import Path
import sqlite3
import time
from typing import TypeAlias, cast
import uuid

from codex_discord_store_connection import connect_store
from codex_discord_store_schema import init_store_schema


SQLiteCell: TypeAlias = str | int | float | bytes | None
SQLiteRow: TypeAlias = tuple[SQLiteCell, ...]
ATTEMPT_COLUMNS = (
    "attempt_id, job_id, attempt_number, app_server_generation, "
    "app_server_process_id, target_thread_id, client_request_id, state, "
    "baseline_turn_ids, turn_id, last_error, progress_at, created_at, updated_at"
)


@unique
class QueueAttemptState(StrEnum):
    EXEC_PENDING = "exec_pending"
    START_PREWRITE = "start_prewrite"
    START_UNKNOWN = "start_unknown"
    RUNNING = "running"
    TURN_TERMINAL = "turn_terminal"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class StoredQueueAttempt:
    attempt_id: str
    job_id: str
    attempt_number: int
    app_server_generation: int
    app_server_process_id: int | None
    target_thread_id: str
    client_request_id: str | None
    state: QueueAttemptState
    baseline_turn_ids: tuple[str, ...]
    turn_id: str | None
    last_error: str
    progress_at: float
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class QueueGenerationReconciliation:
    adopted_job_ids: tuple[str, ...]
    needs_review_job_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LateQueueAttemptReconciliation:
    job_id: str
    target_thread_id: str
    channel_id: int


class QueueAttemptTransitionError(RuntimeError):
    pass


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = connect_store(db_path)
    init_store_schema(conn)
    return conn


def _record(row: SQLiteRow) -> StoredQueueAttempt:
    baseline_raw = json.loads(str(row[8] or "[]"))
    baseline = tuple(str(value) for value in baseline_raw) if isinstance(baseline_raw, list) else ()
    return StoredQueueAttempt(
        attempt_id=str(row[0]),
        job_id=str(row[1]),
        attempt_number=int(cast(int, row[2])),
        app_server_generation=int(cast(int, row[3])),
        app_server_process_id=int(cast(int, row[4])) if row[4] is not None else None,
        target_thread_id=str(row[5]),
        client_request_id=str(row[6]) if row[6] is not None else None,
        state=QueueAttemptState(str(row[7])),
        baseline_turn_ids=baseline,
        turn_id=str(row[9]) if row[9] is not None else None,
        last_error=str(row[10] or ""),
        progress_at=float(cast(float, row[11])),
        created_at=float(cast(float, row[12])),
        updated_at=float(cast(float, row[13])),
    )


def _select_attempt(conn: sqlite3.Connection, attempt_id: str) -> StoredQueueAttempt:
    row = cast(
        SQLiteRow | None,
        conn.execute(
            f"SELECT {ATTEMPT_COLUMNS} FROM codex_turn_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone(),
    )
    if row is None:
        raise LookupError(f"Queue execution attempt not found: {attempt_id}")
    return _record(row)


def begin_queue_execution_attempt(
    db_path: Path,
    job_id: str,
    *,
    app_server_generation: int,
    baseline_turn_ids: tuple[str, ...],
) -> StoredQueueAttempt:
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        job = cast(
            tuple[str] | None,
            conn.execute(
                "SELECT target_thread_id FROM codex_turn_queue "
                "WHERE job_id = ? AND app_server_generation = ?",
                (job_id, app_server_generation),
            ).fetchone(),
        )
        if job is None:
            raise LookupError(f"Queue job not found in generation {app_server_generation}: {job_id}")
        row = cast(
            tuple[int] | None,
            conn.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM codex_turn_attempts WHERE job_id = ?",
                (job_id,),
            ).fetchone(),
        )
        attempt_number = int(row[0] if row is not None else 0) + 1
        now = time.time()
        attempt_id = str(uuid.uuid4())
        _ = conn.execute(
            "INSERT INTO codex_turn_attempts "
            "(attempt_id, job_id, attempt_number, app_server_generation, target_thread_id, "
            "state, baseline_turn_ids, progress_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                job_id,
                attempt_number,
                app_server_generation,
                job[0],
                QueueAttemptState.EXEC_PENDING.value,
                json.dumps(baseline_turn_ids),
                now,
                now,
                now,
            ),
        )
        _ = conn.execute(
            "UPDATE codex_turn_queue SET state = 'starting', "
            "attempt_count = ?, baseline_turn_ids = ?, turn_id = NULL, updated_at = ? "
            "WHERE job_id = ? AND app_server_generation = ?",
            (attempt_number, json.dumps(baseline_turn_ids), now, job_id, app_server_generation),
        )
        return _select_attempt(conn, attempt_id)


def _transition(
    db_path: Path,
    attempt_id: str,
    *,
    from_states: tuple[QueueAttemptState, ...],
    to_state: QueueAttemptState,
    client_request_id: str | None = None,
    app_server_process_id: int | None = None,
    turn_id: str | None = None,
    last_error: str | None = None,
) -> StoredQueueAttempt:
    assignments = ["state = ?", "progress_at = ?", "updated_at = ?"]
    now = time.time()
    params: list[SQLiteCell] = [to_state.value, now, now]
    for column, value in (
        ("client_request_id", client_request_id),
        ("app_server_process_id", app_server_process_id),
        ("turn_id", turn_id),
        ("last_error", last_error),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)
    placeholders = ", ".join("?" for _ in from_states)
    params.extend((attempt_id, *(state.value for state in from_states)))
    with _connect(db_path) as conn:
        result = conn.execute(
            f"UPDATE codex_turn_attempts SET {', '.join(assignments)} "
            f"WHERE attempt_id = ? AND state IN ({placeholders})",
            params,
        )
        if result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} cannot transition to {to_state.value}."
            )
        return _select_attempt(conn, attempt_id)


def mark_queue_attempt_prewrite(
    db_path: Path,
    attempt_id: str,
    *,
    client_request_id: str,
    app_server_process_id: int,
) -> StoredQueueAttempt:
    return _transition(
        db_path,
        attempt_id,
        from_states=(QueueAttemptState.EXEC_PENDING,),
        to_state=QueueAttemptState.START_PREWRITE,
        client_request_id=client_request_id,
        app_server_process_id=app_server_process_id,
    )


def mark_queue_attempt_write_crossed(db_path: Path, attempt_id: str) -> StoredQueueAttempt:
    return _transition(
        db_path,
        attempt_id,
        from_states=(QueueAttemptState.START_PREWRITE,),
        to_state=QueueAttemptState.START_UNKNOWN,
    )


def mark_queue_attempt_running(
    db_path: Path,
    attempt_id: str,
    *,
    turn_id: str,
) -> StoredQueueAttempt:
    return _transition(
        db_path,
        attempt_id,
        from_states=(QueueAttemptState.START_UNKNOWN,),
        to_state=QueueAttemptState.RUNNING,
        turn_id=turn_id,
    )


def mark_queue_execution_running(
    db_path: Path,
    *,
    job_id: str,
    attempt_id: str,
    app_server_generation: int,
    turn_id: str,
) -> StoredQueueAttempt:
    now = time.time()
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        attempt_row = cast(
            tuple[int, str, str | None] | None,
            conn.execute(
                "SELECT attempt_number, state, turn_id FROM codex_turn_attempts "
                "WHERE attempt_id = ? AND job_id = ? AND app_server_generation = ?",
                (attempt_id, job_id, app_server_generation),
            ).fetchone(),
        )
        queue_row = cast(
            tuple[str, int, str | None] | None,
            conn.execute(
                "SELECT state, attempt_count, turn_id FROM codex_turn_queue "
                "WHERE job_id = ? AND app_server_generation = ?",
                (job_id, app_server_generation),
            ).fetchone(),
        )
        if attempt_row is None or queue_row is None:
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} or job {job_id} is missing."
            )
        attempt_number, attempt_state, attempt_turn_id = attempt_row
        queue_state, queue_attempt_count, queue_turn_id = queue_row
        if (
            attempt_state == QueueAttemptState.RUNNING.value
            and queue_state == "running"
            and attempt_turn_id == turn_id
            and queue_turn_id == turn_id
            and queue_attempt_count == attempt_number
        ):
            return _select_attempt(conn, attempt_id)
        if (
            attempt_state != QueueAttemptState.START_UNKNOWN.value
            or queue_state != "starting"
            or attempt_turn_id not in (None, turn_id)
            or queue_turn_id not in (None, turn_id)
            or queue_attempt_count != attempt_number
        ):
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} and job {job_id} have conflicting running state."
            )
        attempt_result = conn.execute(
            "UPDATE codex_turn_attempts SET state = ?, turn_id = ?, progress_at = ?, updated_at = ? "
            "WHERE attempt_id = ? AND job_id = ? AND app_server_generation = ? AND state = ?",
            (
                QueueAttemptState.RUNNING.value,
                turn_id,
                now,
                now,
                attempt_id,
                job_id,
                app_server_generation,
                QueueAttemptState.START_UNKNOWN.value,
            ),
        )
        if attempt_result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} cannot atomically start job {job_id}."
            )
        queue_result = conn.execute(
            "UPDATE codex_turn_queue SET state = 'running', turn_id = ?, updated_at = ? "
            "WHERE job_id = ? AND app_server_generation = ? AND state = 'starting' "
            "AND attempt_count = ? AND (turn_id IS NULL OR turn_id = ?)",
            (
                turn_id,
                now,
                job_id,
                app_server_generation,
                attempt_number,
                turn_id,
            ),
        )
        if queue_result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Queue job {job_id} cannot atomically enter running state."
            )
        return _select_attempt(conn, attempt_id)


def reconcile_late_queue_attempt_running(
    db_path: Path,
    *,
    client_request_id: str,
    app_server_process_id: int,
    app_server_generation: int,
    target_thread_id: str,
    turn_id: str,
) -> LateQueueAttemptReconciliation | None:
    now = time.time()
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        row = cast(
            tuple[str, str] | None,
            conn.execute(
                "SELECT attempt_id, job_id FROM codex_turn_attempts "
                "WHERE client_request_id = ? AND app_server_process_id = ? "
                "AND app_server_generation = ? AND target_thread_id = ? "
                "AND state IN (?, ?)",
                (
                    client_request_id,
                    app_server_process_id,
                    app_server_generation,
                    target_thread_id,
                    QueueAttemptState.START_UNKNOWN.value,
                    QueueAttemptState.NEEDS_REVIEW.value,
                ),
            ).fetchone(),
        )
        if row is None:
            return None
        attempt_id, job_id = row
        attempt_result = conn.execute(
            "UPDATE codex_turn_attempts SET state = ?, turn_id = ?, last_error = '', "
            "progress_at = ?, updated_at = ? WHERE attempt_id = ? "
            "AND client_request_id = ? AND app_server_process_id = ? "
            "AND app_server_generation = ? AND target_thread_id = ? "
            "AND state IN (?, ?)",
            (
                QueueAttemptState.RUNNING.value,
                turn_id,
                now,
                now,
                attempt_id,
                client_request_id,
                app_server_process_id,
                app_server_generation,
                target_thread_id,
                QueueAttemptState.START_UNKNOWN.value,
                QueueAttemptState.NEEDS_REVIEW.value,
            ),
        )
        queue_result = conn.execute(
            "UPDATE codex_turn_queue SET state = 'running', turn_id = ?, last_error = '', "
            "updated_at = ? WHERE job_id = ? AND app_server_generation = ?",
            (turn_id, now, job_id, app_server_generation),
        )
        if attempt_result.rowcount != 1 or queue_result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Late turn/start response cannot atomically resume job {job_id}."
            )
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'promoted', updated_at = ? "
            "WHERE queue_job_id = ? AND state = 'needs_review'",
            (now, job_id),
        )
        delivery_row = cast(
            tuple[str, int] | None,
            conn.execute(
                "SELECT target_thread_id, channel_id FROM codex_turn_queue WHERE job_id = ?",
                (job_id,),
            ).fetchone(),
        )
        if delivery_row is None:
            raise QueueAttemptTransitionError(
                f"Late turn/start response repaired missing queue job {job_id}."
            )
        return LateQueueAttemptReconciliation(
            job_id=job_id,
            target_thread_id=str(delivery_row[0]),
            channel_id=int(delivery_row[1]),
        )


def mark_queue_attempt_terminal(db_path: Path, attempt_id: str) -> StoredQueueAttempt:
    return _transition(
        db_path,
        attempt_id,
        from_states=(QueueAttemptState.RUNNING,),
        to_state=QueueAttemptState.TURN_TERMINAL,
    )


def mark_queue_attempt_needs_review(
    db_path: Path,
    attempt_id: str,
    *,
    last_error: str,
) -> StoredQueueAttempt:
    now = time.time()
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        result = conn.execute(
            "UPDATE codex_turn_attempts SET state = ?, last_error = ?, progress_at = ?, updated_at = ? "
            "WHERE attempt_id = ? AND state IN (?, ?, ?)",
            (
                QueueAttemptState.NEEDS_REVIEW.value,
                last_error,
                now,
                now,
                attempt_id,
                QueueAttemptState.EXEC_PENDING.value,
                QueueAttemptState.START_PREWRITE.value,
                QueueAttemptState.START_UNKNOWN.value,
            ),
        )
        if result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} cannot transition to needs_review."
            )
        attempt = _select_attempt(conn, attempt_id)
        _ = conn.execute(
            "UPDATE codex_turn_queue SET last_error = ?, updated_at = ? WHERE job_id = ?",
            (last_error, now, attempt.job_id),
        )
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'needs_review', updated_at = ? "
            "WHERE queue_job_id = ? AND state = 'promoted'",
            (now, attempt.job_id),
        )
        return attempt


def resolve_queue_attempt_failure(
    db_path: Path,
    attempt_id: str,
    *,
    last_error: str,
) -> StoredQueueAttempt:
    now = time.time()
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        attempt = _select_attempt(conn, attempt_id)
        if attempt.state in (
            QueueAttemptState.START_PREWRITE,
            QueueAttemptState.START_UNKNOWN,
        ):
            _ = conn.execute(
                "UPDATE codex_turn_attempts SET state = ?, last_error = ?, "
                "progress_at = ?, updated_at = ? WHERE attempt_id = ? AND state IN (?, ?)",
                (
                    QueueAttemptState.NEEDS_REVIEW.value,
                    last_error,
                    now,
                    now,
                    attempt_id,
                    QueueAttemptState.START_PREWRITE.value,
                    QueueAttemptState.START_UNKNOWN.value,
                ),
            )
            attempt = _select_attempt(conn, attempt_id)
            _ = conn.execute(
                "UPDATE codex_turn_queue SET last_error = ?, updated_at = ? WHERE job_id = ?",
                (last_error, now, attempt.job_id),
            )
            _ = conn.execute(
                "UPDATE deferred_discord_inbox SET state = 'needs_review', updated_at = ? "
                "WHERE queue_job_id = ? AND state = 'promoted'",
                (now, attempt.job_id),
            )
        return attempt


def complete_queue_execution_attempt(
    db_path: Path,
    *,
    job_id: str,
    attempt_id: str,
) -> None:
    now = time.time()
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        attempt_result = conn.execute(
            "UPDATE codex_turn_attempts SET state = ?, progress_at = ?, updated_at = ? "
            "WHERE attempt_id = ? AND job_id = ? AND state = ?",
            (
                QueueAttemptState.TURN_TERMINAL.value,
                now,
                now,
                attempt_id,
                job_id,
                QueueAttemptState.RUNNING.value,
            ),
        )
        if attempt_result.rowcount != 1:
            raise QueueAttemptTransitionError(
                f"Queue attempt {attempt_id} cannot complete job {job_id}."
            )
        _ = conn.execute(
            "UPDATE deferred_discord_inbox SET state = 'completed', updated_at = ? "
            "WHERE queue_job_id = ? AND state = 'promoted'",
            (now, job_id),
        )
        queue_result = conn.execute(
            "DELETE FROM codex_turn_queue WHERE job_id = ?",
            (job_id,),
        )
        if queue_result.rowcount != 1:
            raise LookupError(f"Queue job not found while completing attempt: {job_id}")


def list_queue_execution_attempts(db_path: Path) -> list[StoredQueueAttempt]:
    with _connect(db_path) as conn:
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {ATTEMPT_COLUMNS} FROM codex_turn_attempts "
                "ORDER BY created_at, attempt_id"
            ).fetchall(),
        )
    return [_record(row) for row in rows]


def get_latest_queue_execution_attempt(
    db_path: Path,
    job_id: str,
) -> StoredQueueAttempt | None:
    with _connect(db_path) as conn:
        row = cast(
            SQLiteRow | None,
            conn.execute(
                f"SELECT {ATTEMPT_COLUMNS} FROM codex_turn_attempts WHERE job_id = ? "
                "ORDER BY attempt_number DESC LIMIT 1",
                (job_id,),
            ).fetchone(),
        )
    return _record(row) if row is not None else None


def reconcile_queue_jobs_for_generation(
    db_path: Path,
    app_server_generation: int,
) -> QueueGenerationReconciliation:
    adopted: list[str] = []
    needs_review: list[str] = []
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        jobs = cast(
            list[tuple[str, int, str]],
            conn.execute(
                "SELECT job_id, app_server_generation, state FROM codex_turn_queue "
                "WHERE app_server_generation != ? ORDER BY created_at, job_id",
                (app_server_generation,),
            ).fetchall(),
        )
        for job_id, _old_generation, queue_state in jobs:
            latest = cast(
                tuple[str, str, str | None] | None,
                conn.execute(
                    "SELECT attempt_id, state, turn_id FROM codex_turn_attempts WHERE job_id = ? "
                    "ORDER BY attempt_number DESC LIMIT 1",
                    (job_id,),
                ).fetchone(),
            )
            safe_pending = (
                latest is None and queue_state == "pending"
            ) or (
                latest is not None and latest[1] == QueueAttemptState.EXEC_PENDING.value
            )
            safe_running = (
                latest is not None
                and latest[1] == QueueAttemptState.RUNNING.value
                and bool(latest[2])
            )
            if safe_pending or safe_running:
                next_queue_state = "running" if safe_running else "pending"
                next_turn_id = latest[2] if safe_running and latest is not None else None
                _ = conn.execute(
                    "UPDATE codex_turn_queue SET app_server_generation = ?, state = ?, "
                    "turn_id = ?, updated_at = ? WHERE job_id = ?",
                    (
                        app_server_generation,
                        next_queue_state,
                        next_turn_id,
                        time.time(),
                        job_id,
                    ),
                )
                if latest is not None:
                    _ = conn.execute(
                        "UPDATE codex_turn_attempts SET app_server_generation = ?, updated_at = ? "
                        "WHERE attempt_id = ?",
                        (app_server_generation, time.time(), latest[0]),
                    )
                adopted.append(job_id)
                continue
            if latest is not None:
                _ = conn.execute(
                    "UPDATE codex_turn_attempts SET state = ?, last_error = ?, updated_at = ? "
                    "WHERE attempt_id = ?",
                    (
                        QueueAttemptState.NEEDS_REVIEW.value,
                        "generation ended after the turn/start write boundary",
                        time.time(),
                        latest[0],
                    ),
                )
            reason = "generation ended after the turn/start write boundary"
            _ = conn.execute(
                "UPDATE codex_turn_queue SET last_error = ?, updated_at = ? WHERE job_id = ?",
                (reason, time.time(), job_id),
            )
            _ = conn.execute(
                "UPDATE deferred_discord_inbox SET state = 'needs_review', updated_at = ? "
                "WHERE queue_job_id = ? AND state = 'promoted'",
                (time.time(), job_id),
            )
            needs_review.append(job_id)
    return QueueGenerationReconciliation(tuple(adopted), tuple(needs_review))
