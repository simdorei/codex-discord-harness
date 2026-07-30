from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import anyio
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import SecretStr

from remote_mcp_server.simdorei_mcp.oauth_store import OAuthStore

READ_SCOPE = "files:read"
WRITE_SCOPE = "files:write"
OAUTH_SCOPES = [READ_SCOPE, WRITE_SCOPE]


class ApprovalNotFoundError(Exception):
    pass


class ApprovalDeniedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float
    failed_attempts: int = 0


class SingleUserOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(
        self,
        *,
        store: OAuthStore,
        owner_token: SecretStr,
        public_base_url: str,
        resource_url: str,
        access_token_seconds: int,
        refresh_token_seconds: int,
    ) -> None:
        self._store = store
        self._owner_token = owner_token
        self._public_base_url = public_base_url.rstrip("/")
        self._resource_url = resource_url
        self._access_token_seconds = access_token_seconds
        self._refresh_token_seconds = refresh_token_seconds
        self._pending: dict[str, PendingAuthorization] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = anyio.Lock()

    async def close(self) -> None:
        await self._store.close()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await self._store.get_client(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._store.save_client(client_info)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        if client.client_id is None:
            raise ValueError("Registered OAuth client is missing client_id.")
        if params.resource not in (None, self._resource_url):
            from mcp.server.auth.provider import AuthorizeError

            raise AuthorizeError("invalid_request", "Unknown protected resource.")
        request_id = secrets.token_urlsafe(32)
        pending = PendingAuthorization(
            client_id=client.client_id,
            params=params,
            expires_at=time.time() + 300,
        )
        async with self._lock:
            self._discard_expired()
            self._pending[request_id] = pending
        return f"{self._public_base_url}/oauth/approve?{urlencode({'request_id': request_id})}"

    async def approve(self, request_id: str, candidate_owner_token: str) -> str:
        async with self._lock:
            self._discard_expired()
            pending = self._pending.get(request_id)
            if pending is None:
                raise ApprovalNotFoundError
            matches = hmac.compare_digest(
                candidate_owner_token,
                self._owner_token.get_secret_value(),
            )
            if not matches:
                attempts = pending.failed_attempts + 1
                if attempts >= 5:
                    self._pending.pop(request_id, None)
                else:
                    self._pending[request_id] = PendingAuthorization(
                        client_id=pending.client_id,
                        params=pending.params,
                        expires_at=pending.expires_at,
                        failed_attempts=attempts,
                    )
                raise ApprovalDeniedError
            self._pending.pop(request_id, None)
            code_value = secrets.token_urlsafe(32)
            code = AuthorizationCode(
                code=code_value,
                scopes=pending.params.scopes or OAUTH_SCOPES,
                expires_at=time.time() + 300,
                client_id=pending.client_id,
                code_challenge=pending.params.code_challenge,
                redirect_uri=pending.params.redirect_uri,
                redirect_uri_provided_explicitly=(
                    pending.params.redirect_uri_provided_explicitly
                ),
                resource=pending.params.resource or self._resource_url,
                subject="owner",
            )
            self._codes[code_value] = code
        return _append_query(
            str(pending.params.redirect_uri),
            code=code_value,
            state=pending.params.state,
        )

    async def pending_exists(self, request_id: str) -> bool:
        async with self._lock:
            self._discard_expired()
            return request_id in self._pending

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        async with self._lock:
            self._discard_expired()
            code = self._codes.get(authorization_code)
            if code is None or code.client_id != client.client_id:
                return None
            return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        async with self._lock:
            stored = self._codes.pop(authorization_code.code, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError("invalid_grant", "Authorization code was already used.")
        access, refresh, family_id = self._new_token_pair(
            client_id=stored.client_id,
            scopes=stored.scopes,
            subject=stored.subject or "owner",
            resource=stored.resource or self._resource_url,
        )
        await self._store.save_token_pair(access, refresh, family_id)
        return self._oauth_token(access, refresh)

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        token = await self._store.load_refresh_token(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if client.client_id is None:
            raise TokenError("invalid_client", "OAuth client_id is missing.")
        access, new_refresh, family_id = self._new_token_pair(
            client_id=client.client_id,
            scopes=scopes,
            subject=refresh_token.subject or "owner",
            resource=self._resource_url,
        )
        rotated = await self._store.rotate_token_pair(
            refresh_token.token,
            access,
            new_refresh,
            family_id,
        )
        if not rotated:
            raise TokenError("invalid_grant", "Refresh token was already used.")
        return self._oauth_token(access, new_refresh)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await self._store.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._store.revoke_family(token.token)

    def _new_token_pair(
        self,
        *,
        client_id: str,
        scopes: list[str],
        subject: str,
        resource: str,
    ) -> tuple[AccessToken, RefreshToken, str]:
        issued_at = int(time.time())
        access = AccessToken(
            token=secrets.token_urlsafe(48),
            client_id=client_id,
            scopes=scopes,
            expires_at=issued_at + self._access_token_seconds,
            resource=resource,
            subject=subject,
        )
        refresh = RefreshToken(
            token=secrets.token_urlsafe(48),
            client_id=client_id,
            scopes=scopes,
            expires_at=issued_at + self._refresh_token_seconds,
            subject=subject,
        )
        return access, refresh, secrets.token_urlsafe(24)

    def _oauth_token(
        self,
        access: AccessToken,
        refresh: RefreshToken,
    ) -> OAuthToken:
        return OAuthToken(
            access_token=access.token,
            expires_in=self._access_token_seconds,
            scope=" ".join(access.scopes),
            refresh_token=refresh.token,
        )

    def _discard_expired(self) -> None:
        now = time.time()
        self._pending = {
            key: value for key, value in self._pending.items() if value.expires_at >= now
        }
        self._codes = {
            key: value for key, value in self._codes.items() if value.expires_at >= now
        }


def _append_query(url: str, **values: str | None) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value is not None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
