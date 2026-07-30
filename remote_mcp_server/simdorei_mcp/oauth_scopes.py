from __future__ import annotations

READ_SCOPE = "files:read"
WRITE_SCOPE = "files:write"
COMPUTER_OBSERVE_SCOPE = "computer:observe"
COMPUTER_CONTROL_SCOPE = "computer:control"
DEFAULT_OAUTH_SCOPES = [READ_SCOPE, WRITE_SCOPE]
COMPUTER_OBSERVE_REQUIRED_SCOPES = (
    READ_SCOPE,
    COMPUTER_OBSERVE_SCOPE,
)
COMPUTER_CONTROL_REQUIRED_SCOPES = (
    READ_SCOPE,
    COMPUTER_OBSERVE_SCOPE,
    COMPUTER_CONTROL_SCOPE,
)
OAUTH_SCOPES = [
    READ_SCOPE,
    WRITE_SCOPE,
    COMPUTER_OBSERVE_SCOPE,
    COMPUTER_CONTROL_SCOPE,
]


class OAuthProviderConfigurationError(Exception):
    """Raised when OAuth client state is internally inconsistent."""
