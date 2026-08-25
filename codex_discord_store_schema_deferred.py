from __future__ import annotations

import sqlite3
from typing import cast


_DEFERRED_INBOX_TABLE_STATEMENT = (
    "CREATE TABLE deferred_discord_inbox ("
    "message_id INTEGER PRIMARY KEY, "
    "target_thread_id TEXT NOT NULL, "
    "channel_id INTEGER NOT NULL, "
    "owner_user_id INTEGER, "
    "prompt TEXT NOT NULL, "
    "source TEXT NOT NULL, "
    "normalization_version INTEGER NOT NULL, "
    "state TEXT NOT NULL CHECK(state IN ("
    "'received', 'promoted', 'completed', 'failed', 'cancelled', 'needs_review')), "
    "queue_job_id TEXT UNIQUE, "
    "promotion_epoch INTEGER, "
    "created_at REAL NOT NULL, "
    "updated_at REAL NOT NULL)"
)
_DEFERRED_INBOX_INDEX_STATEMENT = (
    "CREATE INDEX IF NOT EXISTS deferred_discord_inbox_target_order "
    "ON deferred_discord_inbox(target_thread_id, channel_id, state, created_at, message_id)"
)
_DEFERRED_INBOX_LEASES_TABLE_STATEMENT = (
    "CREATE TABLE deferred_discord_inbox_leases ("
    "target_thread_id TEXT NOT NULL, "
    "channel_id INTEGER NOT NULL, "
    "lease_owner TEXT NOT NULL, "
    "lease_epoch INTEGER NOT NULL, "
    "lease_expires_at REAL NOT NULL, "
    "updated_at REAL NOT NULL, "
    "PRIMARY KEY(target_thread_id, channel_id))"
)


DEFERRED_SCHEMA_TABLES: tuple[str, ...] = (
    "deferred_discord_inbox",
    "deferred_discord_inbox_leases",
    "codex_turn_attempts",
)

DEFERRED_SCHEMA_STATEMENTS: tuple[str, ...] = (
    _DEFERRED_INBOX_TABLE_STATEMENT.replace(
        "CREATE TABLE ",
        "CREATE TABLE IF NOT EXISTS ",
        1,
    ),
    _DEFERRED_INBOX_INDEX_STATEMENT,
    _DEFERRED_INBOX_LEASES_TABLE_STATEMENT.replace(
        "CREATE TABLE ",
        "CREATE TABLE IF NOT EXISTS ",
        1,
    ),
    (
        "CREATE TABLE IF NOT EXISTS codex_turn_attempts ("
        "attempt_id TEXT PRIMARY KEY, "
        "job_id TEXT NOT NULL, "
        "attempt_number INTEGER NOT NULL, "
        "app_server_generation INTEGER NOT NULL, "
        "app_server_process_id INTEGER, "
        "target_thread_id TEXT NOT NULL, "
        "client_request_id TEXT UNIQUE, "
        "state TEXT NOT NULL CHECK(state IN ("
        "'exec_pending', 'start_prewrite', 'start_unknown', 'running', "
        "'turn_terminal', 'needs_review')), "
        "baseline_turn_ids TEXT NOT NULL, "
        "turn_id TEXT, "
        "last_error TEXT NOT NULL DEFAULT '', "
        "progress_at REAL NOT NULL, "
        "created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL, "
        "UNIQUE(job_id, attempt_number))"
    ),
    (
        "CREATE INDEX IF NOT EXISTS codex_turn_attempts_job_order "
        "ON codex_turn_attempts(job_id, attempt_number DESC)"
    ),
)


def migrate_deferred_delivery_schema(conn: sqlite3.Connection) -> None:
    for statement in DEFERRED_SCHEMA_STATEMENTS:
        _ = conn.execute(statement)


def migrate_deferred_inbox_lease_scope(conn: sqlite3.Connection) -> None:
    table_info = list(
        conn.execute("PRAGMA table_info(deferred_discord_inbox_leases)")
    )
    primary_key = tuple(
        str(row[1])
        for row in sorted(table_info, key=lambda row: int(row[5]))
        if int(row[5]) > 0
    )
    if primary_key == ("target_thread_id", "channel_id"):
        return
    _ = conn.execute("DROP TABLE IF EXISTS deferred_discord_inbox_leases")
    _ = conn.execute(_DEFERRED_INBOX_LEASES_TABLE_STATEMENT)


def migrate_deferred_inbox_state_constraint(conn: sqlite3.Connection) -> None:
    row = cast(
        tuple[str] | None,
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            + "AND name = 'deferred_discord_inbox'"
        ).fetchone(),
    )
    if row is None:
        raise sqlite3.OperationalError("deferred_discord_inbox table is missing")
    schema_sql = row[0]
    if "'failed'" in schema_sql and "'cancelled'" in schema_sql:
        return
    replacement_table = "deferred_discord_inbox_v5"
    columns = (
        "message_id, target_thread_id, channel_id, owner_user_id, prompt, source, "
        "normalization_version, state, queue_job_id, promotion_epoch, created_at, updated_at"
    )
    _ = conn.execute(
        _DEFERRED_INBOX_TABLE_STATEMENT.replace(
            "deferred_discord_inbox",
            replacement_table,
            1,
        )
    )
    _ = conn.execute(
        f"INSERT INTO {replacement_table} ({columns}) SELECT {columns} "
        + "FROM deferred_discord_inbox"
    )
    _ = conn.execute("DROP TABLE deferred_discord_inbox")
    _ = conn.execute(
        f"ALTER TABLE {replacement_table} RENAME TO deferred_discord_inbox"
    )
    _ = conn.execute(_DEFERRED_INBOX_INDEX_STATEMENT)
