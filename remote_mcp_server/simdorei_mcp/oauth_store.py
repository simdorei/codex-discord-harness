from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from anyio import to_thread
from mcp.server.auth.provider import AccessToken, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull


class OAuthStore:
    """Small persistent store for one-user OAuth clients and hashed tokens."""

    def __init__(self, database_path: Path) -> None:
        if str(database_path) != ":memory:":
            database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(database_path),
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._initialize()

    async def close(self) -> None:
        await to_thread.run_sync(self._close)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await to_thread.run_sync(self._get_client, client_id)

    async def save_client(self, client: OAuthClientInformationFull) -> None:
        await to_thread.run_sync(self._save_client, client)

    async def save_token_pair(
        self,
        access: AccessToken,
        refresh: RefreshToken,
        family_id: str,
    ) -> None:
        await to_thread.run_sync(
            self._save_token_pair,
            access,
            refresh,
            family_id,
        )

    async def rotate_token_pair(
        self,
        old_refresh_token: str,
        access: AccessToken,
        refresh: RefreshToken,
        family_id: str,
    ) -> bool:
        return await to_thread.run_sync(
            self._rotate_token_pair,
            old_refresh_token,
            access,
            refresh,
            family_id,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await to_thread.run_sync(self._load_token, token, "access")
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def load_refresh_token(self, token: str) -> RefreshToken | None:
        row = await to_thread.run_sync(self._load_token, token, "refresh")
        if row is None:
            return None
        return RefreshToken(
            token=token,
            client_id=row["client_id"],
            scopes=json.loads(row["scopes_json"]),
            expires_at=row["expires_at"],
            subject=row["subject"],
        )

    async def revoke_family(self, token: str) -> None:
        await to_thread.run_sync(self._revoke_family, token)

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS oauth_clients (
                    client_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    token_kind TEXT NOT NULL,
                    family_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    expires_at INTEGER,
                    resource TEXT,
                    subject TEXT
                );
                CREATE INDEX IF NOT EXISTS oauth_tokens_family
                    ON oauth_tokens(family_id);
                """
            )

    def _close(self) -> None:
        with self._lock:
            self._connection.close()

    def _get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM oauth_clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["payload"])

    def _save_client(self, client: OAuthClientInformationFull) -> None:
        if client.client_id is None:
            raise ValueError("OAuth client_id is required.")
        payload = client.model_dump_json()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO oauth_clients(client_id, payload) VALUES (?, ?)",
                (client.client_id, payload),
            )

    def _save_token_pair(
        self,
        access: AccessToken,
        refresh: RefreshToken,
        family_id: str,
    ) -> None:
        with self._lock, self._connection:
            self._insert_token(access, "access", family_id)
            self._insert_token(refresh, "refresh", family_id)

    def _rotate_token_pair(
        self,
        old_refresh_token: str,
        access: AccessToken,
        refresh: RefreshToken,
        family_id: str,
    ) -> bool:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT family_id FROM oauth_tokens WHERE token_hash = ? AND token_kind = 'refresh'",
                (_token_hash(old_refresh_token),),
            ).fetchone()
            if row is None:
                return False
            self._connection.execute(
                "DELETE FROM oauth_tokens WHERE family_id = ?",
                (row["family_id"],),
            )
            self._insert_token(access, "access", family_id)
            self._insert_token(refresh, "refresh", family_id)
            return True

    def _insert_token(
        self,
        token: AccessToken | RefreshToken,
        kind: str,
        family_id: str,
    ) -> None:
        resource = token.resource if isinstance(token, AccessToken) else None
        self._connection.execute(
            """
            INSERT INTO oauth_tokens(
                token_hash, token_kind, family_id, client_id, scopes_json,
                expires_at, resource, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _token_hash(token.token),
                kind,
                family_id,
                token.client_id,
                json.dumps(token.scopes),
                token.expires_at,
                resource,
                token.subject,
            ),
        )

    def _load_token(self, token: str, kind: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM oauth_tokens WHERE token_hash = ? AND token_kind = ?",
                (_token_hash(token), kind),
            ).fetchone()

    def _revoke_family(self, token: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT family_id FROM oauth_tokens WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is not None:
                self._connection.execute(
                    "DELETE FROM oauth_tokens WHERE family_id = ?",
                    (row["family_id"],),
                )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
