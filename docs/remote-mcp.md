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
- Each `!pro` call registers a non-secret project scope for 30 minutes by default.
- Each Codex thread gets a stable, opaque conversation scope.
- The same scope reuses its ChatGPT conversation when that conversation can still
  be found.
- A missing, deleted, or unusable saved conversation is replaced with one new
  conversation.
- A ChatGPT conversation already connected to another Codex thread cannot switch
  projects. A new ChatGPT conversation can select the other active project scope.
- Disconnecting the local bridge revokes the server-side conversation route.
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
- Only fixed test/check commands discovered from supported project manifests are
  exposed. JavaScript package scripts are currently accepted only when their
  literal command is `node --test ...`; Python, Cargo, Go, and Flutter standard
  test commands are also recognized. They run in a workspace-confined,
  network-disabled sandbox. Network, deployment, destructive, and arbitrary
  shell commands are rejected.
- Mouse, keyboard, desktop, and unrestricted shell control are not exposed.

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
mapped Discord thread. Codex opens ChatGPT Pro and includes the non-secret
project scope. ChatGPT calls `select_project` before using the project file
tools. It can then search and edit files, inspect images, run allowlisted
project checks, review Git changes, create commits, and push an already
configured remote. On the first connector use, ChatGPT opens the OAuth approval page; enter
the server's owner token there once. The browser workflow keeps normal Chat mode
with Pro reasoning; it does
not switch to Work, agent mode, deep research, or another model. If the
local-project connector is not offered to normal Pro for the account, the
workflow reports that limitation instead of pretending that files were read or
changed.

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
SIMDOREI_MCP_REQUEST_TIMEOUT_SECONDS=30
SIMDOREI_MCP_LOG_LEVEL=INFO
```

The container binds only to VPS loopback port `8030`; Nginx publishes HTTPS
`/mcp`, the OAuth endpoints, and WebSocket `/bridge`. OAuth clients and hashed
tokens persist in the Docker volume mounted at `/data`.

### Protocol 2 rollout

The expanded project-operation protocol intentionally rejects old bridge or
gateway processes instead of mixing message formats. Upgrade in one coordinated
maintenance window:

1. Stop the local bridge.
2. Deploy the gateway that reports bridge protocol 2.
3. Restart the updated local bridge immediately.
4. Confirm `/healthz`, one authenticated `select_project`, and one read-only
   `project_status` round trip before allowing write tools.

The bridge reconnects automatically. During the short gap, project tools fail
closed rather than being routed to an incompatible local process.
