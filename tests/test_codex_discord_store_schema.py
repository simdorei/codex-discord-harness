from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import codex_discord_store as store
import codex_discord_store_schema as store_schema


def _create_v4_deferred_inbox(conn: sqlite3.Connection) -> None:
    _ = conn.execute(
        "CREATE TABLE deferred_discord_inbox ("
        "message_id INTEGER PRIMARY KEY, "
        "target_thread_id TEXT NOT NULL, "
        "channel_id INTEGER NOT NULL, "
        "owner_user_id INTEGER, "
        "prompt TEXT NOT NULL, "
        "source TEXT NOT NULL, "
        "normalization_version INTEGER NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ("
        "'received', 'promoted', 'completed', 'needs_review')), "
        "queue_job_id TEXT UNIQUE, "
        "promotion_epoch INTEGER, "
        "created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )


def _create_six_state_deferred_inbox(conn: sqlite3.Connection) -> None:
    _ = conn.execute(
        "CREATE TABLE deferred_discord_inbox ("
        "message_id INTEGER PRIMARY KEY, target_thread_id TEXT NOT NULL, "
        "channel_id INTEGER NOT NULL, owner_user_id INTEGER, prompt TEXT NOT NULL, "
        "source TEXT NOT NULL, normalization_version INTEGER NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ("
        "'received', 'promoted', 'completed', 'failed', 'cancelled', 'needs_review')), "
        "queue_job_id TEXT UNIQUE, promotion_epoch INTEGER, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    _ = conn.execute(
        "CREATE INDEX deferred_discord_inbox_target_order "
        "ON deferred_discord_inbox("
        "target_thread_id, channel_id, state, created_at, message_id)"
    )


class StoreSchemaTests(unittest.TestCase):
    def test_store_schema_helper_creates_expected_tables(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            store_schema.init_store_schema(conn)
            table_rows = cast(
                list[tuple[str]],
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall(),
            )
            tables = {
                row[0] for row in table_rows
            }

        self.assertEqual(tables, set(store_schema.STORE_SCHEMA_TABLES))

    def test_schema_records_latest_version_and_passes_integrity_check(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            store_schema.init_store_schema(conn)

            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])

        self.assertEqual(version, store_schema.LATEST_STORE_SCHEMA_VERSION)
        self.assertEqual(integrity, "ok")

    def test_public_init_mirror_db_preserves_representative_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            store.init_mirror_db(db_path)
            with sqlite3.connect(db_path) as conn:
                project_rows = cast(
                    list[tuple[int, str, str, int, object, int]],
                    conn.execute("PRAGMA table_info(mirror_projects)").fetchall(),
                )
                busy_choice_rows = cast(
                    list[tuple[int, str, str, int, object, int]],
                    conn.execute("PRAGMA table_info(busy_choices)").fetchall(),
                )
                session_event_rows = cast(
                    list[tuple[int, str, str, int, object, int]],
                    conn.execute(
                        "PRAGMA table_info(codex_session_mirror_events)"
                    ).fetchall(),
                )
                project_columns = {
                    row[1] for row in project_rows
                }
                busy_choice_columns = {
                    row[1] for row in busy_choice_rows
                }
                session_event_columns = {
                    row[1] for row in session_event_rows
                }
                detail_columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(session_mirror_details)"
                    ).fetchall()
                }

        self.assertEqual(
            project_columns,
            {"project_key", "project_name", "discord_channel_id", "updated_at"},
        )
        self.assertEqual(
            busy_choice_columns,
            {
                "choice_id",
                "owner_user_id",
                "channel_id",
                "target_thread_id",
                "prompt",
                "allow_steer",
                "created_at",
                "expires_at",
                "claimed_at",
            },
        )
        self.assertEqual(
            session_event_columns,
            {"event_digest", "codex_thread_id", "created_at"},
        )
        self.assertEqual(
            detail_columns,
            {
                "codex_thread_id",
                "detail_mode",
            },
        )

    def test_legacy_database_is_backed_up_once_before_migration(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute("CREATE TABLE legacy_value (value TEXT NOT NULL)")
                _ = conn.execute("INSERT INTO legacy_value VALUES ('preserved')")

            store.init_mirror_db(db_path)
            store.init_mirror_db(db_path)
            backups = list((db_path.parent / ".codex-discord-backups").glob("*.sqlite"))
            with sqlite3.connect(backups[0]) as backup_conn:
                backup_value = str(
                    backup_conn.execute("SELECT value FROM legacy_value").fetchone()[0]
                )
                backup_version = int(backup_conn.execute("PRAGMA user_version").fetchone()[0])

        self.assertEqual(len(backups), 1)
        self.assertEqual(backup_value, "preserved")
        self.assertEqual(backup_version, 0)

    def test_v3_target_lease_migrates_to_per_channel_scope(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            _create_v4_deferred_inbox(conn)
            _ = conn.execute(
                "CREATE TABLE deferred_discord_inbox_leases ("
                "target_thread_id TEXT PRIMARY KEY, "
                "lease_owner TEXT NOT NULL, "
                "lease_epoch INTEGER NOT NULL, "
                "lease_expires_at REAL NOT NULL, "
                "updated_at REAL NOT NULL)"
            )
            _ = conn.execute(
                "INSERT INTO deferred_discord_inbox_leases VALUES "
                "('thread-1', 'stale-owner', 7, 10.0, 1.0)"
            )
            _ = conn.execute("PRAGMA user_version = 3")
            conn.commit()

            store_schema.init_store_schema(conn)

            table_info = list(
                conn.execute("PRAGMA table_info(deferred_discord_inbox_leases)")
            )
            lease_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM deferred_discord_inbox_leases"
                ).fetchone()[0]
            )

        self.assertEqual(
            {str(row[1]) for row in table_info},
            {
                "target_thread_id",
                "channel_id",
                "lease_owner",
                "lease_epoch",
                "lease_expires_at",
                "updated_at",
            },
        )
        self.assertEqual(
            [str(row[1]) for row in table_info if int(row[5]) > 0],
            ["target_thread_id", "channel_id"],
        )
        self.assertEqual(lease_rows, 0)

    def test_v4_inbox_state_constraint_migrates_without_losing_rows(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            _create_v4_deferred_inbox(conn)
            _ = conn.execute(
                "INSERT INTO deferred_discord_inbox VALUES "
                "(901, 'thread-1', 222, 7, 'first', 'gateway', 1, "
                "'promoted', 'discord:901', 3, 1.0, 2.0), "
                "(902, 'thread-1', 222, 7, 'second', 'gateway', 1, "
                "'promoted', 'discord:902', 3, 3.0, 4.0)"
            )
            _ = conn.execute("PRAGMA user_version = 4")
            conn.commit()

            store_schema.init_store_schema(conn)
            _ = conn.execute(
                "UPDATE deferred_discord_inbox SET state = 'failed' WHERE message_id = 901"
            )
            _ = conn.execute(
                "UPDATE deferred_discord_inbox SET state = 'cancelled' WHERE message_id = 902"
            )
            rows = list(
                conn.execute(
                    "SELECT message_id, prompt, state FROM deferred_discord_inbox "
                    "ORDER BY message_id"
                )
            )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])

        self.assertEqual(version, store_schema.LATEST_STORE_SCHEMA_VERSION)
        self.assertEqual(
            rows,
            [(901, "first", "failed"), (902, "second", "cancelled")],
        )

    def test_live_six_state_v4_compatibility_schema_promotes_to_v5_in_place(self) -> None:
        expected_row = (
            901,
            "thread-1",
            222,
            7,
            "preserve bytes: \x00\u2603",
            "gateway",
            1,
            "failed",
            "discord:901",
            3,
            1.25,
            2.5,
        )
        with sqlite3.connect(":memory:") as conn:
            _create_six_state_deferred_inbox(conn)
            _ = conn.execute(
                "INSERT INTO deferred_discord_inbox VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                expected_row,
            )
            _ = conn.execute("PRAGMA user_version = 4")
            conn.commit()

            store_schema.init_store_schema(conn)

            actual_row = tuple(
                conn.execute(
                    "SELECT * FROM deferred_discord_inbox WHERE message_id = 901"
                ).fetchone()
            )
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            table_sql = str(
                conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'deferred_discord_inbox'"
                ).fetchone()[0]
            )
            index_sql = str(
                conn.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'deferred_discord_inbox_target_order'"
                ).fetchone()[0]
            )
            with self.assertRaises(sqlite3.IntegrityError):
                _ = conn.execute(
                    "INSERT INTO deferred_discord_inbox VALUES "
                    "(902, 'thread-2', 223, 8, 'duplicate', 'gateway', 1, "
                    "'received', 'discord:901', 4, 3.0, 4.0)"
                )

        self.assertEqual(version, store_schema.LATEST_STORE_SCHEMA_VERSION)
        self.assertEqual(actual_row, expected_row)
        self.assertIn("'failed'", table_sql)
        self.assertIn("'cancelled'", table_sql)
        self.assertIn(
            "target_thread_id, channel_id, state, created_at, message_id",
            index_sql,
        )

    def test_failed_migration_rolls_back_and_leaves_backup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            db_path = Path(temp_dir) / "mirror.sqlite"
            with sqlite3.connect(db_path) as conn:
                _ = conn.execute("CREATE TABLE legacy_value (value TEXT NOT NULL)")

            with patch.object(
                store_schema,
                "assert_store_integrity",
                side_effect=store_schema.StoreIntegrityError("forced failure"),
            ):
                with self.assertRaises(store_schema.StoreIntegrityError):
                    store.init_mirror_db(db_path)

            with sqlite3.connect(db_path) as conn:
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
            backups = list((db_path.parent / ".codex-discord-backups").glob("*.sqlite"))

        self.assertEqual(version, 0)
        self.assertEqual(tables, {"legacy_value"})
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    _ = unittest.main()
