# AI operator runbook: register another PC

Use this runbook when an AI operator must add one Windows PC to the hosted
Simdorei MCP gateway. Registration and online presence are different:

- **registered** means the VPS credential registry contains the PC;
- **online** means that PC is currently running the local bridge and has an
  accepted WebSocket connection to the gateway.

The ChatGPT connector and OAuth approval are shared. Adding a PC does not
require creating another connector or deleting the OAuth database.

## Required inputs

Obtain these before changing anything. Do not guess missing values.

- the SSH target for the existing VPS;
- the active Docker Compose directory on that VPS;
- the new PC's unique, public-safe device ID;
- the absolute repository directory on the new PC;
- authorized access to update the VPS private `.env` and the new PC `.env`.

The device ID must use ASCII letters, numbers, `.`, `_`, or `-`. Prefer a name
that identifies the machine without containing a person's name or another
private value.

## Non-negotiable safety rules

1. Never print, paste, commit, upload, attach, or log a device token, the owner
   token, the full credential registry, OAuth codes, cookies, or session data.
2. Never pass a token as a command-line argument. Process listings and shell
   history can retain command arguments. Transfer secret values through a
   process-local pipe or a permission-restricted temporary file and delete that
   file after the two private `.env` files are updated.
3. Read the existing VPS registry and preserve every existing device entry.
   Append or deliberately replace only the requested device ID.
4. Give every PC a different device ID and a different cryptographically random
   token. Reusing a pair makes the newer connection replace the older one.
5. Do not create a second gateway, database, Docker volume, or Nginx route.
6. Do not run `docker compose down -v` and do not delete the `/data` OAuth
   volume. Device registration needs only a private environment update and a
   gateway container recreation.
7. If the local repository is dirty, the Compose deployment cannot be located
   confidently, the current registry is invalid, or adding the device would
   exceed 32 entries, stop and report the exact blocker.

## Procedure

### 1. Establish a rollback point

On the VPS, locate the active Compose project from the running gateway
container's Docker Compose labels. Do not assume a directory, container name,
or loopback port from an older deployment.

Record, without secrets:

- the running image tag or digest;
- the Compose project and service name;
- the current `/healthz` values;
- the current registered device IDs only;
- a permission-restricted backup path for the private `.env`.

Copy the private `.env` to that backup path with mode `0600`. Do not display its
contents.

### 2. Prepare the new PC

In the new PC's repository:

1. Confirm the intended branch and a clean working tree.
2. Fetch the remote and update with a fast-forward-only pull. Do not reset or
   overwrite local work.
3. Install or update dependencies with the repository's normal installer.
4. Generate a unique token with a cryptographically secure random generator.
   It must contain 32 to 512 printable ASCII characters.
5. Update only these values in the uncommitted local `.env`:

   ```dotenv
   CODEX_REMOTE_MCP_ENABLED=1
   CODEX_REMOTE_MCP_BRIDGE_URL=wss://simdorei.duckdns.org/bridge
   CODEX_REMOTE_MCP_DEVICE_ID=<new-device-id>
   CODEX_REMOTE_MCP_DEVICE_TOKEN=<new-secret-token>
   ```

Keep `.env` gitignored. Never copy another PC's complete `.env` because it can
contain unrelated credentials and would duplicate the device identity.

### 3. Update the VPS registry

Parse `SIMDOREI_MCP_DEVICE_CREDENTIALS_JSON` as JSON. Require `version` to equal
`1` and `devices` to be a list. Before writing:

- validate that device IDs are unique;
- validate that tokens are unique;
- preserve every existing device ID and token value unchanged;
- append one object containing the new device ID and the same new token used on
  the PC;
- serialize the registry back as one compact JSON object.

Write the updated value atomically to the existing VPS private `.env`. Preserve
its permissions and every unrelated setting. Validate the new JSON without
printing it.

### 4. Apply the registration

Recreate only the existing gateway service through its existing Compose
project so it rereads `.env`. Reuse the already deployed image when only the
registry changed. Do not destroy volumes and do not rebuild unrelated services.

Wait for the gateway health check to become healthy. Then start or restart the
updated local bot on the new PC. For login-time automatic start without a black
console window, point the existing Windows startup mechanism at
`codex-discord-bot-headless.vbs` in that PC's repository.

### 5. Verify the real user path

All of the following must pass:

1. `/healthz` reports the previous `configured_devices` count plus one.
2. After the new bridge starts, `connected_devices` also increases by one.
3. The gateway has one accepted `/bridge` connection for the new start and no
   authorization or protocol rejection.
4. The new PC log contains `remote_mcp_bridge_connected` and no terminal
   `remote_mcp_bridge_rejected` or `remote_mcp_bridge_displaced` event.
5. ChatGPT's existing connector can call `list_devices` and see the new device
   ID as online.
6. Selecting that device and calling a read-only status operation succeeds
   before any write or computer-control action is attempted.

Do not declare success from `configured_devices` alone. That proves only VPS
registration, not that the new PC is online.

## Rollback

If the gateway fails after the registry edit, restore the permission-restricted
VPS `.env` backup and recreate only the gateway service. Keep the OAuth volume.

If the gateway is healthy but the new PC cannot connect, leave existing device
entries unchanged. Stop the new PC bridge, inspect its public-safe error and the
gateway rejection status, then correct only that PC's ID/token pair. Remove the
temporary secret-transfer artifact after success or rollback.

## Completion report for another AI

Return only public-safe evidence:

- deployed image identifier;
- new device ID;
- configured and connected device counts before and after;
- local bridge connected or failed;
- ChatGPT `list_devices` result;
- whether the private backup was retained or removed.

Never include token values, the full registry JSON, `.env` contents, OAuth data,
or raw authorization headers in the report.
