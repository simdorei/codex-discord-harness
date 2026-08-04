from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from typing import Final, Literal, assert_never, cast
from urllib.parse import urlsplit


SCOPE_PATTERN: Final = re.compile(r"^codex-pro-[a-f0-9]{24}$")
CHATGPT_HOSTS: Final = frozenset({"chatgpt.com", "www.chatgpt.com"})
LEASE_SECONDS: Final = 120


class ConversationMapError(Exception):
    """Raised when a conversation mapping command is invalid."""


class _Arguments(argparse.Namespace):
    command: Literal["acquire", "set", "release", "delete"] | None = None
    scope: str | None = None
    url: str | None = None
    lease_token: str | None = None


_AcquireRow = tuple[str | None, str | None, int | None]
_SaveRow = tuple[str | None]
_ReleaseRow = tuple[str | None, str | None]


def database_path() -> Path:
    override = os.environ.get("SIMDOREI_PRO_CONVERSATION_DB", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_data:
        base = Path(local_data)
    else:
        state_home = os.environ.get("XDG_STATE_HOME", "").strip()
        base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / "simdorei" / "ask-chatgpt-pro" / "conversations.sqlite3"


def acquire(scope: str) -> dict[str, str]:
    _validate_scope(scope)
    now = int(time.time())
    with _connect() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            _AcquireRow | None,
            connection.execute(
                """
            SELECT conversation_url, lease_hash, lease_expires_at
            FROM conversations
            WHERE scope = ?
            """,
                (scope,),
            ).fetchone(),
        )
        conversation_url = row[0] if row is not None else None
        if conversation_url:
            connection.commit()
            return {"status": "found", "url": str(conversation_url)}
        if (
            row is not None
            and row[1]
            and (row[2] or 0) >= now
        ):
            connection.commit()
            return {"status": "busy"}
        lease_token = "lease_" + secrets.token_urlsafe(24)
        _ = connection.execute(
            """
            INSERT INTO conversations(
                scope, conversation_url, lease_hash, lease_expires_at, updated_at
            ) VALUES (?, NULL, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                lease_hash = excluded.lease_hash,
                lease_expires_at = excluded.lease_expires_at,
                updated_at = excluded.updated_at
            """,
            (
                scope,
                _token_hash(lease_token),
                now + LEASE_SECONDS,
                now,
            ),
        )
        connection.commit()
    return {"status": "acquired", "lease_token": lease_token}


def save(scope: str, url: str, lease_token: str) -> dict[str, str]:
    _validate_scope(scope)
    _validate_url(url)
    _validate_lease_token(lease_token)
    now = int(time.time())
    with _connect() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            _SaveRow | None,
            connection.execute(
                """
            SELECT lease_hash
            FROM conversations
            WHERE scope = ?
            """,
                (scope,),
            ).fetchone(),
        )
        if row is None or not secrets.compare_digest(
            str(row[0] or ""),
            _token_hash(lease_token),
        ):
            raise ConversationMapError(
                "The conversation creation lease is missing or was replaced."
            )
        _ = connection.execute(
            """
            UPDATE conversations
            SET conversation_url = ?, lease_hash = NULL,
                lease_expires_at = NULL, updated_at = ?
            WHERE scope = ?
            """,
            (url, now, scope),
        )
        connection.commit()
    return {"status": "saved"}


def release(scope: str, lease_token: str) -> dict[str, str]:
    _validate_scope(scope)
    _validate_lease_token(lease_token)
    with _connect() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        row = cast(
            _ReleaseRow | None,
            connection.execute(
                "SELECT lease_hash, conversation_url FROM conversations WHERE scope = ?",
                (scope,),
            ).fetchone(),
        )
        if row is not None and secrets.compare_digest(
            str(row[0] or ""),
            _token_hash(lease_token),
        ):
            if row[1]:
                _ = connection.execute(
                    """
                    UPDATE conversations
                    SET lease_hash = NULL, lease_expires_at = NULL
                    WHERE scope = ?
                    """,
                    (scope,),
                )
            else:
                _ = connection.execute(
                    "DELETE FROM conversations WHERE scope = ?",
                    (scope,),
                )
        connection.commit()
    return {"status": "released"}


def delete(scope: str) -> dict[str, str]:
    _validate_scope(scope)
    with _connect() as connection:
        _ = connection.execute(
            "DELETE FROM conversations WHERE scope = ?",
            (scope,),
        )
    return {"status": "deleted"}


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    _ = connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            scope TEXT PRIMARY KEY,
            conversation_url TEXT,
            lease_hash TEXT,
            lease_expires_at INTEGER,
            updated_at INTEGER NOT NULL
        )
        """
    )
    return connection


def _validate_scope(scope: str) -> None:
    if SCOPE_PATTERN.fullmatch(scope) is None:
        raise ConversationMapError("The conversation scope is invalid.")


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in CHATGPT_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or "/c/" not in parsed.path
    ):
        raise ConversationMapError(
            "Only canonical HTTPS chatgpt.com conversation URLs are allowed."
        )


def _validate_lease_token(lease_token: str) -> None:
    if len(lease_token) < 24:
        raise ConversationMapError("The conversation creation lease is invalid.")


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "delete"):
        command = commands.add_parser(name)
        _ = command.add_argument("--scope", required=True)
    save_command = commands.add_parser("set")
    _ = save_command.add_argument("--scope", required=True)
    _ = save_command.add_argument("--url", required=True)
    _ = save_command.add_argument("--lease-token", required=True)
    release_command = commands.add_parser("release")
    _ = release_command.add_argument("--scope", required=True)
    _ = release_command.add_argument("--lease-token", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args(namespace=_Arguments())
    try:
        if arguments.command is None or arguments.scope is None:
            raise ConversationMapError("The command arguments are incomplete.")
        match arguments.command:
            case "acquire":
                result = acquire(arguments.scope)
            case "set":
                if arguments.url is None or arguments.lease_token is None:
                    raise ConversationMapError("The set command arguments are incomplete.")
                result = save(
                    arguments.scope,
                    arguments.url,
                    arguments.lease_token,
                )
            case "release":
                if arguments.lease_token is None:
                    raise ConversationMapError(
                        "The release command arguments are incomplete."
                    )
                result = release(arguments.scope, arguments.lease_token)
            case "delete":
                result = delete(arguments.scope)
            case _:
                assert_never(arguments.command)
    except ConversationMapError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
