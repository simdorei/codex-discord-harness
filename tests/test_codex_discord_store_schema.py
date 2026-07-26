from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import cast

import codex_discord_store as store
import codex_discord_store_schema as store_schema


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


if __name__ == "__main__":
    _ = unittest.main()
