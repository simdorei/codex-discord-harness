# ChatGPT Pro local-project MCP

This optional path lets a ChatGPT conversation inspect or update the project
belonging to the Codex thread that issued `!pro`.

## Connection model

The local bot opens an outbound encrypted WebSocket to the hosted gateway:

```text
local project <- local bridge -> simdorei gateway <- MCP -> ChatGPT conversation
```

The PC does not need a fixed public IP, DuckDNS, router port forwarding, or an
incoming firewall rule. It does need to keep this repository's bot process
running because a hosted server cannot directly read files that remain on a PC.

## Safety boundaries

- ChatGPT connects to the VPS through OAuth 2.1. The owner token is entered only
  on the VPS approval page and is never copied into a model-visible chat.
- Each `!pro` call registers a fresh, unguessable project scope for 30 minutes
  by default. A newer registration for the same Codex thread revokes the older
  scope and its selected ChatGPT session.
- Treat that project scope as a temporary access capability: do not copy, quote,
  log, or share it outside the connector's `select_project` call.
- Each Codex thread gets a stable, opaque conversation scope.
- The same scope reuses its ChatGPT conversation when that conversation can still
  be found.
- A missing, deleted, or unusable saved conversation is replaced with one new
  conversation.
- A ChatGPT conversation already connected to another Codex thread cannot switch
  projects. A new ChatGPT conversation can select the other active project scope.
- Disconnecting the local bridge revokes the server-side conversation route.
- OAuth route ownership includes both the approved connector and account owner,
  so another connector cannot impersonate a saved ChatGPT session name.
- Paths cannot escape the bound project.
- `.env`, credential, key, cookie, and common secret paths are blocked.
- Files are UTF-8 text and at most 1 MiB.
- Likely credential values are replaced with `[REDACTED]` in model-visible
  reads, search results, diffs, checkpoint previews, command output, and errors.
  File reads report `redacted=true` when this happened; a whole redacted file
  must not be reconstructed and overwritten from that output.
- Updating an existing file requires the SHA-256 returned by the last read.
- Every exposed file create, write, patch, move, delete, and image-save operation
  creates a restorable checkpoint.
- PNG, JPEG, GIF, and WebP images can be saved, listed, and returned to ChatGPT
  as native visual input. Each image is limited to 5 MiB.
- External image downloads accept public HTTPS only and reject local/private
  network destinations.
- Git status, diff, commit, and push use the project's existing Git
  configuration and credentials. Credentials are never sent through the MCP.
- Fixed test/check commands remain available as the bounded `command_run` path.
  A connector with both file read and file write authority can also use
  `terminal_exec` for unrestricted PowerShell, cmd, sh, or bash text with the
  local bot process's host permissions. It can launch child processes and use an
  explicit absolute working directory outside the selected project. Terminal
  execution remains bound to the selected ChatGPT session and returns a
  public-safe receipt without prompting for per-command approval.
- During an active project binding, ChatGPT can launch, list, and activate an
  isolated Chrome or blank Notepad window owned by that selection. Notepad can
  return native screenshots, click, drag, scroll, type Unicode text, press
  allowlisted keys, set clipboard text, request a normal window close, and
  trigger an emergency stop.
- Every pointer, typing, key, clipboard, and close action requires a fresh screenshot token.
  The token expires after 30 seconds, belongs to one window, and is consumed by
  one action, so stale coordinates cannot be replayed.
- Selecting the project from a new ChatGPT conversation or OAuth connector
  revokes screenshot tokens issued to the previous selection for that thread.
- The selected ChatGPT session can use `terminal_window_open`,
  `terminal_window_list`, `terminal_window_capture`, `terminal_window_activate`,
  `terminal_window_type`, `terminal_window_keys`, `terminal_window_interrupt`,
  and `terminal_window_close` on visible terminal windows it owns. Every
  state-changing terminal-window action is bound to a fresh capture observation.
  Password managers, ChatGPT/Codex, remote-desktop apps, Windows security
  surfaces, sign-in/password/OTP windows, and credential extraction remain
  outside the supported surface. Clipboard contents cannot be read.
- `stop_computer_control` disables computer tools for that Codex thread until a
  new `!pro` binding renews it. Binding expiry or bridge disconnection also
  revokes computer control. Rebinding or stopping also closes every app process
  launched by the old selection; isolated Chrome profile removal retries while
  Windows releases short-lived file locks. Cleanup remains owned by the session
  and is retried on the next stop instead of being abandoned in the background.
- Only genuine Chrome and classic single-document Notepad executables launched
  by the current ChatGPT selection from their standard Windows installation
  paths are controllable. Pre-existing user windows are invisible to the connector.
  Window names returned to ChatGPT are coarse app labels rather than document
  or page titles.
- Chrome can be launched, listed, activated, and removed by emergency stop, but
  its pixels are never returned to ChatGPT. A web page controls its own title,
  so titles cannot prove that rendered pixels exclude authentication, CAPTCHA,
  consent, or secret surfaces. Notepad supports the screenshot-bound editing
  action set.
- Computer observation and computer control use dedicated OAuth scopes. A
  connector authorized before these scopes were added must be reconnected and
  approved again; an old file-only token cannot acquire desktop authority.
- Terminal execution and terminal-window interaction reuse the existing
  `files:read` plus `files:write` authority. An already authorized project
  connector therefore receives the full session-owned terminal surface without
  a second OAuth approval.
- Window screenshots are captured from the selected window handle, not from the
  desktop underneath overlays. Keyboard and pointer actions are sent to the
  verified Notepad editor control rather than global Windows input. Input is
  rejected if the process, executable path, bounds, document content/title,
  binding, or short-lived observation changes.
- A new selection or rebind is acknowledged only after every already-admitted
  operation for the old generation finishes and the previous controller stops.
  Queued old screenshots or actions are cancelled, and every command that
  arrives with the old generation after that boundary is rejected locally.
- If a launched window disappears while its process is still winding down, it
  is omitted from the controllable list but retained only for identity-checked
  cleanup. If the process is still running but the original window identity can
  no longer be proved, forced termination is refused. Failed process cleanup
  stays retryable and is reported instead of being acknowledged as complete.

This is a single-owner gateway. OAuth protects the ChatGPT-to-VPS side; the
existing device token separately protects the VPS-to-local-PC bridge.

## Local configuration

Keep these values only in the uncommitted `.env` file:

```dotenv
CODEX_REMOTE_MCP_ENABLED=1
CODEX_REMOTE_MCP_BRIDGE_URL=wss://simdorei.duckdns.org/bridge
CODEX_REMOTE_MCP_DEVICE_ID=your-private-device-id
CODEX_REMOTE_MCP_DEVICE_TOKEN=your-private-device-token
CODEX_REMOTE_MCP_BINDING_TTL_SECONDS=1800
```

Install or update dependencies with the normal project installer, then restart
the bot.

## ChatGPT configuration

Add the MCP server URL once in ChatGPT developer/connectors settings:

```text
https://simdorei.duckdns.org/mcp
```

After that, send `!pro <question>` or `!pro review <review request>` from a
mapped Discord thread. Codex opens ChatGPT Pro and includes the short-lived
project scope. ChatGPT calls `select_project` before using the project tools. It
can then search and edit files, inspect images, run fixed project checks or
unrestricted terminal commands, operate visible session-owned terminal windows,
review Git changes, create commits, and push an already configured remote. On
the first connector use, ChatGPT opens the OAuth approval page; enter
the server's owner token there once. The browser workflow keeps normal Chat mode
with Pro reasoning; it does
not switch to Work, agent mode, deep research, or another model. If the
local-project connector is not offered to normal Pro for the account, the
workflow reports that limitation instead of pretending that files were read or
changed.

For computer control, ChatGPT first launches Chrome or Notepad, then lists and
activates that session-owned window. It takes screenshots only from Notepad.
One returned observation ID authorizes one matching Notepad UI action for 30
seconds. After each action it takes another screenshot. Chrome pixels, UAC,
login, CAPTCHA, password, and OTP surfaces are never returned to ChatGPT.

## Hosted deployment

The gateway container lives in `remote_mcp_server/`. Its private `.env` requires:

```dotenv
SIMDOREI_MCP_DEVICE_ID=the-same-device-id
SIMDOREI_MCP_DEVICE_TOKEN=the-same-device-token
SIMDOREI_MCP_PUBLIC_BASE_URL=https://simdorei.duckdns.org
SIMDOREI_MCP_OWNER_TOKEN=a-separate-random-secret-at-least-24-characters
SIMDOREI_MCP_OAUTH_DATABASE_PATH=/data/oauth.sqlite3
SIMDOREI_MCP_OAUTH_ACCESS_TOKEN_SECONDS=3600
SIMDOREI_MCP_OAUTH_REFRESH_TOKEN_SECONDS=2592000
SIMDOREI_MCP_OAUTH_PENDING_AUTHORIZATION_LIMIT=100
SIMDOREI_MCP_OAUTH_AUTHORIZATION_CODE_GLOBAL_LIMIT=1024
SIMDOREI_MCP_OAUTH_AUTHORIZATION_CODE_PER_CLIENT_LIMIT=64
SIMDOREI_MCP_OAUTH_CLIENT_LIMIT=500
SIMDOREI_MCP_OAUTH_TOKEN_FAMILY_GLOBAL_LIMIT=256
SIMDOREI_MCP_OAUTH_TOKEN_FAMILY_PER_CLIENT_LIMIT=16
SIMDOREI_MCP_OAUTH_REFRESH_HISTORY_GLOBAL_LIMIT=65536
SIMDOREI_MCP_OAUTH_REFRESH_HISTORY_PER_FAMILY_LIMIT=1024
SIMDOREI_MCP_REQUEST_TIMEOUT_SECONDS=3630
SIMDOREI_MCP_LOG_LEVEL=INFO
```

The container binds only to VPS loopback port `8030`; Nginx publishes HTTPS
`/mcp`, the OAuth endpoints, and WebSocket `/bridge`. OAuth clients and hashed
tokens persist in the Docker volume mounted at `/data`.

Refresh-token rotation also retains a bounded history of spent token hashes.
Reusing any spent token revokes the currently active token family. If either
history limit is reached, that family is revoked instead of weakening replay
detection, and the connector must complete authorization again.

The Dockerfile pins its Python and `uv` build images by readable version tag and
multi-platform digest so a later rebuild cannot silently change either input.
These pins do not update automatically: when applying a security update, verify
the new release and change its version tag and digest together before rebuilding.

### Protocol 9 rollout

The expanded project-operation protocol intentionally rejects old bridge or
gateway processes instead of mixing message formats. Upgrade in one coordinated
maintenance window:

1. Stop the local bridge.
2. Deploy the gateway that reports bridge protocol 9.
3. Restart the updated local bridge immediately.
4. Confirm `/healthz`, one authenticated `select_project`, and one read-only
   `project_status` round trip before allowing write tools.

The bridge reconnects automatically. During the short gap, project tools fail
closed rather than being routed to an incompatible local process.

If rollout or the authenticated smoke check fails, roll both sides back in the
same maintenance window:

1. Stop the local bridge so no mixed protocol traffic can be admitted.
2. Restore the previous gateway source or image and start the existing Compose
   project again. Keep the private `.env` and the named `/data` OAuth volume.
3. Restore the local repository to the matching previous commit.
4. Restart the local bridge.
5. Confirm `/healthz`, one authenticated `select_project`, and one read-only
   `project_status` round trip before enabling writes again.

Do not leave only one side rolled back: protocol mismatches are deliberately
rejected, and the persistent OAuth database must not be deleted during rollback.
