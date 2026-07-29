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

- Each `!pro` call creates a random, one-time project binding.
- The binding expires after 30 minutes by default.
- Each Codex thread gets a stable, opaque conversation scope.
- The same scope reuses its ChatGPT conversation when that conversation can still
  be found.
- A missing, deleted, or unusable saved conversation is replaced with one new
  conversation.
- A ChatGPT conversation already connected to another Codex thread cannot switch
  projects. The unused binding code can instead be used in a new conversation.
- Disconnecting the local bridge revokes the server-side conversation route.
- Paths cannot escape the bound project.
- `.env`, credential, key, cookie, and common secret paths are blocked.
- Files are UTF-8 text and at most 1 MiB.
- Updating an existing file requires the SHA-256 returned by the last read.
- Arbitrary shell commands are not exposed.

The current gateway is a private MVP. Before allowing unrelated public users,
add per-user OAuth authorization and separate device enrollment.

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
mapped Discord thread. Codex opens ChatGPT Pro and includes the one-time binding
instruction. ChatGPT must call `bind_project` before using the project file
tools. The browser workflow keeps normal Chat mode with Pro reasoning; it does
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
SIMDOREI_MCP_REQUEST_TIMEOUT_SECONDS=30
SIMDOREI_MCP_LOG_LEVEL=INFO
```

The container binds only to VPS loopback port `8030`; Nginx publishes HTTPS
`/mcp` and WebSocket `/bridge`.
