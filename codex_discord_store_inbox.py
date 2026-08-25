from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
import sqlite3
import time
from typing import TypeAlias, cast

from codex_discord_store_connection import connect_store
from codex_discord_store_queue import QUEUE_JOB_COLUMNS, StoredQueueJob, _record as queue_record
from codex_discord_store_schema import init_store_schema


SQLiteCell: TypeAlias = str | int | float | bytes | None
SQLiteRow: TypeAlias = tuple[SQLiteCell, ...]
INBOX_COLUMNS = (
    "message_id, target_thread_id, channel_id, owner_user_id, prompt, source, "
    "normalization_version, state, queue_job_id, promotion_epoch, created_at, updated_at"
)


@unique
class DeferredInboxState(StrEnum):
    RECEIVED = "received"
    PROMOTED = "promoted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True, slots=True)
class DeferredInboxRecord:
    message_id: int
    target_thread_id: str
    channel_id: int
    owner_user_id: int | None
    prompt: str
    source: str
    normalization_version: int
    state: DeferredInboxState
    queue_job_id: str | None
    promotion_epoch: int | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class DeferredInboxClaim:
    record: DeferredInboxRecord
    created: bool


@dataclass(frozen=True, slots=True)
class DeferredInboxPromotion:
    jobs: tuple[StoredQueueJob, ...]
    lease_epoch: int


class DeferredInboxConflictError(RuntimeError):
    pass


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = connect_store(db_path)
    init_store_schema(conn)
    return conn


def _record(row: SQLiteRow) -> DeferredInboxRecord:
    return DeferredInboxRecord(
        message_id=int(cast(int, row[0])),
        target_thread_id=str(row[1]),
        channel_id=int(cast(int, row[2])),
        owner_user_id=int(cast(int, row[3])) if row[3] is not None else None,
        prompt=str(row[4]),
        source=str(row[5]),
        normalization_version=int(cast(int, row[6])),
        state=DeferredInboxState(str(row[7])),
        queue_job_id=str(row[8]) if row[8] is not None else None,
        promotion_epoch=int(cast(int, row[9])) if row[9] is not None else None,
        created_at=float(cast(float, row[10])),
        updated_at=float(cast(float, row[11])),
    )


def _select_message(conn: sqlite3.Connection, message_id: int) -> DeferredInboxRecord:
    row = cast(
        SQLiteRow | None,
        conn.execute(
            f"SELECT {INBOX_COLUMNS} FROM deferred_discord_inbox WHERE message_id = ?",
            (message_id,),
        ).fetchone(),
    )
    if row is None:
        raise LookupError(f"Deferred Discord inbox message not found: {message_id}")
    return _record(row)


def claim_deferred_discord_message(
    db_path: Path,
    *,
    message_id: int,
    target_thread_id: str,
    channel_id: int,
    owner_user_id: int | None,
    prompt: str,
    source: str,
    normalization_version: int,
    created_at: float | None = None,
) -> DeferredInboxClaim:
    now = time.time() if created_at is None else created_at
    immutable = (
        target_thread_id,
        channel_id,
        owner_user_id,
        prompt,
        source,
        normalization_version,
    )
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            "INSERT OR IGNORE INTO deferred_discord_inbox "
            "(message_id, target_thread_id, channel_id, owner_user_id, prompt, source, "
            "normalization_version, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, *immutable, DeferredInboxState.RECEIVED.value, now, now),
        ).rowcount == 1
        record = _select_message(conn, message_id)
        actual = (
            record.target_thread_id,
            record.channel_id,
            record.owner_user_id,
            record.prompt,
            record.source,
            record.normalization_version,
        )
        if actual != immutable:
            raise DeferredInboxConflictError(
                f"Discord message {message_id} conflicts with its immutable inbox payload."
            )
        _ = conn.execute(
            "INSERT OR IGNORE INTO discord_processed_messages (message_id, seen_at) VALUES (?, ?)",
            (message_id, now),
        )
    return DeferredInboxClaim(record, inserted)


def list_deferred_discord_messages(
    db_path: Path,
    *,
    state: DeferredInboxState | None = None,
) -> list[DeferredInboxRecord]:
    where = " WHERE state = ?" if state is not None else ""
    params: tuple[str, ...] = (state.value,) if state is not None else ()
    with _connect(db_path) as conn:
        rows = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {INBOX_COLUMNS} FROM deferred_discord_inbox{where} "
                "ORDER BY created_at, message_id",
                params,
            ).fetchall(),
        )
    return [_record(row) for row in rows]


def has_pending_deferred_discord_messages(
    db_path: Path,
    target_thread_id: str,
    channel_id: int,
) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM deferred_discord_inbox WHERE target_thread_id = ? "
            "AND channel_id = ? AND state = ? LIMIT 1",
            (target_thread_id, channel_id, DeferredInboxState.RECEIVED.value),
        ).fetchone()
    return row is not None


def promote_deferred_discord_messages(
    db_path: Path,
    *,
    target_thread_id: str,
    channel_id: int,
    app_server_generation: int,
    lease_owner: str,
    now: float | None = None,
    lease_seconds: float = 30.0,
) -> DeferredInboxPromotion:
    current = time.time() if now is None else now
    with _connect(db_path) as conn:
        _ = conn.execute("BEGIN IMMEDIATE")
        pending = cast(
            list[SQLiteRow],
            conn.execute(
                f"SELECT {INBOX_COLUMNS} FROM deferred_discord_inbox "
                "WHERE target_thread_id = ? AND channel_id = ? AND state = ? "
                "ORDER BY created_at, message_id",
                (target_thread_id, channel_id, DeferredInboxState.RECEIVED.value),
            ).fetchall(),
        )
        if not pending:
            return DeferredInboxPromotion((), 0)
        _ = conn.execute(
            "INSERT INTO deferred_discord_inbox_leases "
            "(target_thread_id, channel_id, lease_owner, lease_epoch, lease_expires_at, updated_at) "
            "VALUES (?, ?, ?, 1, ?, ?) ON CONFLICT(target_thread_id, channel_id) DO UPDATE SET "
            "lease_owner = excluded.lease_owner, lease_epoch = lease_epoch + 1, "
            "lease_expires_at = excluded.lease_expires_at, updated_at = excluded.updated_at "
            "WHERE lease_owner = excluded.lease_owner OR lease_expires_at <= ?",
            (
                target_thread_id,
                channel_id,
                lease_owner,
                current + lease_seconds,
                current,
                current,
            ),
        )
        lease = cast(
            tuple[str, int] | None,
            conn.execute(
                "SELECT lease_owner, lease_epoch FROM deferred_discord_inbox_leases "
                "WHERE target_thread_id = ? AND channel_id = ?",
                (target_thread_id, channel_id),
            ).fetchone(),
        )
        if lease is None or lease[0] != lease_owner:
            return DeferredInboxPromotion((), 0)
        epoch = int(lease[1])
        promoted_rows: list[SQLiteRow] = []
        for row in pending:
            record = _record(row)
            job_id = f"discord:{record.message_id}"
            _ = conn.execute(
                "INSERT OR IGNORE INTO codex_turn_queue "
                "(job_id, target_thread_id, channel_id, owner_user_id, discord_message_id, "
                "app_server_generation, prompt, queued, ack_sent, state, attempt_count, "
                "baseline_turn_ids, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'pending', 0, '[]', ?, ?)",
                (
                    job_id,
                    record.target_thread_id,
                    record.channel_id,
                    record.owner_user_id,
                    record.message_id,
                    app_server_generation,
                    record.prompt,
                    record.created_at,
                    current,
                ),
            )
            queue_row = cast(
                SQLiteRow,
                conn.execute(
                    f"SELECT {QUEUE_JOB_COLUMNS} FROM codex_turn_queue WHERE discord_message_id = ?",
                    (record.message_id,),
                ).fetchone(),
            )
            queued_record = queue_record(queue_row)
            if (
                queued_record.job_id != job_id
                or queued_record.target_thread_id != record.target_thread_id
                or queued_record.channel_id != record.channel_id
                or queued_record.owner_user_id != record.owner_user_id
                or queued_record.prompt != record.prompt
            ):
                raise DeferredInboxConflictError(
                    f"Discord message {record.message_id} conflicts with its queue payload."
                )
            promoted_rows.append(queue_row)
            _ = conn.execute(
                "UPDATE deferred_discord_inbox SET state = ?, queue_job_id = ?, "
                "promotion_epoch = ?, updated_at = ? WHERE message_id = ? AND state = ?",
                (
                    DeferredInboxState.PROMOTED.value,
                    job_id,
                    epoch,
                    current,
                    record.message_id,
                    DeferredInboxState.RECEIVED.value,
                ),
            )
        _ = conn.execute(
            "UPDATE deferred_discord_inbox_leases SET lease_expires_at = ?, updated_at = ? "
            "WHERE target_thread_id = ? AND channel_id = ? "
            "AND lease_owner = ? AND lease_epoch = ?",
            (current, current, target_thread_id, channel_id, lease_owner, epoch),
        )
    return DeferredInboxPromotion(tuple(queue_record(row) for row in promoted_rows), epoch)
