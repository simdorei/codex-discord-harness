# Codex Discord Remote

Local Discord remote control for Codex Desktop.

This repo lets a Discord bot send messages into local Codex Desktop threads, mirror Codex replies back to Discord, and expose common Codex operations through Discord commands. It is not an official OpenAI client, hosted relay, or npm package. It runs on the user's own machine, where Codex Desktop is already installed, signed in, awake, and trusted.

## AI Agent Brief

- Product: local bridge between Discord and Codex Desktop.
- Main user: a person who wants to operate their local Codex app from Discord.
- Runtime model: Discord bot process plus local Codex Desktop/app-server access.
- Primary platform: Windows 10/11.
- macOS status: native code path exists and macOS GitHub Actions smoke passes, but real GUI control requires Accessibility permission and may still need manual Mac QA on the target machine.
- Linux status: not a desktop-control target.
- Secrets: `.env` contains the Discord bot token and must not be committed.
- Start here when changing code: `codex_discord_bot.py`, `codex_desktop_bridge.py`, `codex_app_server_transport.py`, and the tests matching the touched file.
- End-to-end flow: [architecture route map](docs/architecture-route-map.md).

## Support Matrix

| Platform | Status | Desktop control method | Launcher |
| --- | --- | --- | --- |
| Windows 10/11 | Primary supported path | Win32 APIs, PowerShell UI Automation, Codex app-server | `.\codex-discord-bot.cmd` |
| macOS | Beta path | AppleScript/System Events, `open`, `pbcopy`, `pbpaste`, Codex app-server | `./codex-discord-bot.sh` |
| Linux | Not supported for Codex Desktop control | None | None |

On macOS, grant Accessibility permission to the terminal app that runs the bot. Without it, System Events cannot focus Codex Desktop, click controls, or send keyboard shortcuts.

## What It Does

- Maps Discord channels and threads to local Codex threads.
- Sends plain Discord messages into the mapped Codex thread.
- Mirrors Codex text, approvals, input prompts, image outputs, and structured file outputs back to Discord.
- Serializes cross-target Codex app turns so one Discord thread does not accidentally abort another.
- Supports slash commands, `!` prefix commands, startup diagnostics, history polling, and Windows tray/watchdog launchers.
- Can run beside other Discord bots. If another bot works in the same Discord thread, mention this bridge when you want local Codex to inspect, steer, or continue that work.

## Setup Overview

1. Create a Discord bot token.
2. Clone this repo.
3. Run the platform installer.
4. Run the token setup script after installation.
5. Invite the bot to the Discord server.
6. Fill the remaining `.env` Discord IDs.
7. Restart Codex Desktop.
8. Start the bot launcher.

The installer can run without the Discord token. Token setup is intentionally post-install so the token is pasted only into the local `.env` file.

## Requirements

- Windows 10/11 or macOS.
- Python 3.12.x. On Windows, `install.ps1` downloads portable Python 3.12.1 into this repo when missing.
- Git.
- Codex Desktop installed and signed in.
- Discord bot token.
- Discord server and channel/thread IDs where the bot may read and send messages.
- Discord Developer Portal `Message Content Intent` enabled if plain text messages should be forwarded.

## Create The Discord Bot Token

1. Open <https://discord.com/developers/applications>.
2. Click **New Application**, name it, and create it.
3. Open **Bot** in the left sidebar.
4. Click **Reset Token** or **Copy Token**.
5. In **Privileged Gateway Intents**, enable **Message Content Intent**.
6. Save changes.

Keep the token private. Do not paste it into chat, commits, GitHub Actions, or issue comments.

## Install

Clone:

```powershell
git clone https://github.com/simdorei/codex-discord-remote.git
cd codex-discord-remote
```

Windows install:

```powershell
.\install.ps1
```

Windows dry run:

```powershell
.\install.ps1 -DryRun
```

macOS install:

```sh
./install.sh
```

macOS dry run:

```sh
./install.sh --dry-run
```

If macOS shell scripts are not executable because the repo was copied from a zip file:

```sh
chmod +x ./install.sh ./setup-discord-bot.sh ./codex-discord-bot.sh
```

Installer behavior:

- Requires Python 3.12.x.
- On Windows, verifies Python and pip bootstrap downloads against `runtime-release.json`, builds them in `.python-portable.stage/`, and activates the staged runtime only after verification succeeds.
- Pins the portable Python executable into `.env` as `PYTHON_EXE`.
- Pins the resolved Codex data directory into `.env` as `CODEX_HOME`.
- Ignores runtime-only Codex `bin`, `.sandbox-bin`, `.plugin-appserver`, and `app/resources` paths when resolving `CODEX_HOME`, and falls back to the real user `.codex` directory.
- Uses a `codex` command inherited from the process environment or found on `PATH` for installation without pinning that discovered path into `.env`. It writes `CODEX_EXE` only when explicitly passed with `-CodexExe` or `--codex-exe`.
- Installs exact, hash-verified Python dependencies from `requirements.txt`; direct dependency ranges remain in `requirements.in`.
- Creates `.env` from `.env.example` if `.env` does not exist.
- Discovers Codex Desktop and writes `CODEX_DESKTOP_EXE` to `.env`, skipping CLI resource executables such as `app/resources/codex.exe`.
- Registers the local Codex plugin marketplace and installs the bundled
  `codex-discord-remote` plugin.
- Re-reads `codex plugin marketplace list --json` and `codex plugin list
  --json`, then verifies the marketplace root plus the plugin's installed,
  enabled, and exact-version state before reporting success.

The default install stops with `INSTALL_INCOMPLETE` when Codex or plugin
installation or inventory verification is unavailable; this prevents a bot
install from being reported as ready while `!pro` is missing or still using a
stale skill. For an intentional bot-only install, use `-SkipCodexPlugin` on
Windows or `--skip-codex-plugin` on macOS/Linux. In that mode, `!pro` is not
ready until the installer is rerun without the skip and Codex is restarted so
the installed skill reloads.

Dry-run mode prints `Dry run complete` and explicitly reports that plugin
inventory was not verified; only a non-dry-run verified install prints `Install
complete`.

### Durable state migration and recovery

The Discord state database keeps its existing path and table names. When its
schema version must change, the bot first writes an online SQLite backup beside
the database under `.codex-discord-backups/`, applies all changes in one
transaction, and runs `PRAGMA integrity_check` before committing. A failed
migration is rolled back and the backup is retained.

To restore a backup, stop the bot, keep the failed database for diagnosis, copy
the selected `.sqlite` backup back to the configured mirror database path, and
restart the bot. The bot never silently restores or discards durable state.

## Add Token And Invite Bot

Windows:

```powershell
.\setup-discord-bot.ps1
```

macOS:

```sh
./setup-discord-bot.sh
```

The setup script:

- asks for the Discord bot token with hidden input
- checks the token with Discord
- saves `DISCORD_BOT_TOKEN` to `.env`
- asks for the general text channel ID for `!` commands, then adds it to `DISCORD_ALLOWED_CHANNEL_IDS` and fills `DISCORD_STARTUP_CHANNEL_ID` when that value is empty
- prints a Discord invite URL
- on Windows, registers and starts the interactive highest-privilege `Codex Discord Bot` scheduled task at logon and every minute; run setup from an Administrator PowerShell window so the bot can control ordinary and administrator app windows

Open the invite URL, choose the Discord server, and authorize the bot. The invite adds the bot to a server; channel access still depends on Discord permissions and `.env` channel IDs.

## Configure `.env`

Turn on Discord **User Settings** -> **Advanced** -> **Developer Mode**. Then right-click the server, channels, threads, and bot user to copy IDs.

Minimum useful settings:

```dotenv
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_discord_server_id
DISCORD_ALLOWED_CHANNEL_IDS=channel_or_thread_id
DISCORD_STARTUP_CHANNEL_ID=channel_id_for_startup_notice
DISCORD_ENABLE_MESSAGE_CONTENT=1
DISCORD_PLAIN_ASK_MENTION_USER_IDS=your_bridge_bot_user_id
DISCORD_SESSION_MIRROR=1
DISCORD_ENABLE_ATTACHMENTS=1
CODEX_DESKTOP_EXE=
CODEX_EXE=
PYTHON_EXE=
```

Important fields:

| Name | Meaning |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Secret bot token. Filled by the setup script. |
| `DISCORD_GUILD_ID` | Discord server ID. |
| `DISCORD_ALLOWED_CHANNEL_IDS` | Comma-separated allowlist of channel or thread IDs. |
| `DISCORD_STARTUP_CHANNEL_ID` | Channel ID for startup notices. Filled by setup when a general channel ID is provided and this field is empty. |
| `DISCORD_ALLOWED_USER_IDS` | Discord user IDs only. Required for host reboot commands. |
| `DISCORD_ENABLE_HOST_COMMANDS` | Default `0`. Keep disabled unless trusted users are allowlisted. |
| `DISCORD_PLAIN_ASK_MENTION_USER_IDS` | Bot user IDs that must be mentioned for plain ask routing outside mapped threads. |
| `DISCORD_SESSION_MIRROR` | Mirrors mapped Codex session output back into Discord. |
| `DISCORD_ENABLE_ATTACHMENTS` | Saves Discord attachments and includes local paths in Codex prompts. |
| `CODEX_EXE` | Optional explicit path to a Codex CLI/app-server executable. Leave it blank to resolve `codex` from `PATH` at runtime. |
| `CODEX_DESKTOP_EXE` | Optional path to Codex Desktop executable. Installer tries to fill it. |
| `PYTHON_EXE` | Python 3.12 executable path. Windows installer pins the repo-local portable Python automatically. |

Do not commit `.env`.

The launchers read `PYTHON_EXE`, `CODEX_HOME`, `CODEX_EXE`, and `CODEX_DESKTOP_EXE` from `.env` before Python starts, so paths with spaces are passed to the bot as normal environment variables.

## Run

Windows visible launcher:

```powershell
.\codex-discord-bot.cmd
```

Windows headless launcher:

```powershell
wscript.exe .\codex-discord-bot-headless.vbs
```

Windows watchdog (registered automatically by `setup-discord-bot.ps1`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\codex-discord-watchdog.ps1
```

The scheduled task runs in the signed-in user's interactive desktop at the highest available privilege. This lets PC mode control ordinary and administrator app windows, while Windows secure-desktop surfaces such as the sign-in screen and Ctrl+Alt+Delete remain unavailable. Overlapping runs are ignored, so a healthy bot remains a single process. The command above is still useful for a manual one-off check.

The watchdog restarts the headless bot if the bot is missing. It also restarts the bot after repeated unhealthy host samples: CPU at or above `95%` for `2` consecutive checks. Free-memory restarting is disabled by default; opt in by passing a positive `-HealthFreeMemoryMb` value. Tune the checks with `-HealthCpuPercent`, `-HealthFreeMemoryMb`, and `-HealthBadSampleLimit`. Put `.codex_discord_bot.disabled` in the repo root to pause watchdog restarts.

macOS launcher:

```sh
./codex-discord-bot.sh
```

Logs:

- bot log: `codex_discord_bot.log`, unless `CODEX_DISCORD_LOG_PATH` is set
- launcher log: `discord_launcher.log`

## Install Or Reinstall Codex Plugin

The installer normally registers the local plugin marketplace. Manual commands:

```powershell
codex plugin marketplace add .
codex plugin add codex-discord-remote@codex-discord-remote
```

Confirm:

```powershell
codex plugin marketplace list
codex plugin list
```

Expected entries:

- marketplace: `codex-discord-remote`
- plugin: `codex-discord-remote@codex-discord-remote`

Restart Codex Desktop after installing the plugin so bundled skills load into new sessions.

## Discord Command Surface

Registered Discord slash commands:

- /help, /list, /archived_list, /use, /status, /settings, /doctor, /where, /context, /usage, /runners, /retract, /mirror_check, /bridge_sync, /new, /ask, /interview (legacy alias: /ask_ipc)

Common slash commands:

- `/help`
- `/list`
- `/status`
- `/doctor`
- `/where`
- `/context`
- `/usage`
- `/new`
- `/ask`
- `/interview`

Common prefix commands:

- `!help`
- `!pro <question>` (optional ChatGPT Pro consultation; local-project MCP setup is documented below)
- `!list`
- `!archived_list`
- `!use`
- `!open`
- `!open_abort`
- `!stop`
- `!resume [ref]`
- `!status`
- `!settings`
- `!doctor`
- `!discover_codex`
- `!restart_codex`
- `!chatid`
- `!where`
- `!context`
- `!usage`
- `!runners`
- `!resources`
- `!retract`
- `!bridge`
- `!mirror`
- `!approval`
- `!archive`
- `!archive-used`
- `!delete_archive`
- `!confirm_delete_archive`
- `!new`
- `!interview`

Bundled Codex skill `deep-interview` powers `/interview` and `!interview` for unclear or risky requests.

For intent-first automated test design or test-first implementation, send a normal prompt in a mapped Discord thread beginning with `$codex-discord-remote:intent-driven-qa`. The skill turns desired behavior into a requirement-to-test contract and records honest RED-to-GREEN evidence. The existing `!qa buttons` command remains reserved for Discord bridge button QA. See [bundled skills and request examples](docs/plugin-skills.md#requesting-intent-driven-qa).

When the optional local-project MCP is connected, `!pro` can also let the
matching ChatGPT Pro conversation inspect screenshots, operate safe Windows app
windows, execute unrestricted shell text, and directly interact with terminal
windows owned by that ChatGPT session while the thread's short-lived binding is
active. Password/OTP and credential extraction remain outside the supported
surface. One gateway can keep up to 32 PCs connected when each PC has its own
device ID and token.
See [docs/remote-mcp.md](docs/remote-mcp.md) for the exact controls and stop rule.

Numeric refs follow the same DB-root numbering as `!list`.

See [docs/operations.md](docs/operations.md) for daily workflow details.

## Send Files To Discord

For one-off files, use the helper instead of pasting binary data or saying file delivery is unavailable:

```powershell
py -3 .\send_discord_attachment.py --thread-ref repo:2 --content-file .\caption.txt .\result.zip
```

Use `--thread-ref` for a mirrored Codex thread, `--work-thread` for a specific Codex thread id/ref, or `--channel-id` when you already know the Discord channel/thread id. Put Korean or multiline captions in a UTF-8 text file and pass it with `--content-file`.

## Routing And Mirror Rules

- A mapped Discord thread follows its matching Codex app thread.
- Mirror discovery includes only local Codex user-root threads. Regular ChatGPT/GPT conversations outside Codex are intentionally excluded.
- Projectless Codex `new-chat` threads remain included as the Codex chat project.
- Plain Discord messages inside a mapped thread go to that mapped Codex thread, not the currently selected Codex tab.
- Codex text output mirrors as Discord text.
- Codex image and structured file output mirrors as Discord attachments.
- Local file uploads are limited to `.codex-remote-attachments` to avoid uploading unrelated files.
- `STOP REPLY` means the Codex app's current response stop button.
- Discord `!stop` is a bot command and is not the same thing as `STOP REPLY`.
- Messages from other bots are ignored unless they explicitly mention this bridge user.

## Source Map For AI Agents

Use this map before editing:

| Area | Files |
| --- | --- |
| Discord bot entrypoint | `codex_discord_bot.py`, `codex_discord_runtime.py` |
| Discord command/runtime modules | `codex_discord_*` |
| Desktop bridge facade | `codex_desktop_bridge.py`, `codex_desktop_bridge_impl_common.py`, `codex_desktop_bridge_impl_chunk*.py` |
| Windows desktop control | `codex_desktop_bridge_windows_input.py`, `codex_desktop_bridge_windows_native.py`, `codex_desktop_bridge_*.ps1` |
| macOS desktop control | `codex_desktop_bridge_macos_input.py`, `codex_desktop_bridge_macos_ui.py` |
| Codex app-server transport | `codex_app_server_transport*.py`, `codex_desktop_bridge_sidecar*.py` |
| Local IPC path | `codex_desktop_bridge_ipc_*.py`, `codex_desktop_bridge_ipc_pipe.py` |
| Session mirror | `codex_discord_session_mirror*.py` |
| Install/setup | `install.ps1`, `install.sh`, `setup_discord_bot.py`, `setup-discord-bot.ps1`, `setup-discord-bot.sh` |
| Launchers | `codex-discord-bot.cmd`, `codex-discord-bot.sh`, `codex-discord-bot-headless.vbs` |
| Tests | `tests/test_*.py` |
| CI | `.github/workflows/macos-smoke.yml` |

Prefer changing the shared helper used by all callers instead of patching one command path.

## Validation

Windows/local smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-remote\scripts\qa-smoke.ps1
```

Focused Python tests:

```powershell
py -3 -m unittest tests.test_setup_discord_bot tests.test_codex_desktop_bridge_desktop_process tests.test_codex_desktop_bridge_macos_input
```

macOS CI smoke runs on push and pull request:

```text
.github/workflows/macos-smoke.yml
```

The macOS smoke workflow checks Python compilation, selected unit tests, `install.sh --dry-run`, and `setup-discord-bot.sh --dry-run`. The Windows contract workflow checks the Windows installer, bridge facade, and durable-store migration contracts.

## Troubleshooting

`Codex CLI was not found`

- Install or enable the `codex` command, or set `CODEX_EXE` in `.env`.
- Bot setup can still continue without plugin registration.
- Installers no longer save a `codex` path discovered from `PATH`. If an older install already wrote a stale path, clear only its value to `CODEX_EXE=`; existing non-empty values are preserved because they may be intentional overrides.

`Discord messages are ignored`

- Check `DISCORD_ENABLE_MESSAGE_CONTENT=1`.
- Check Message Content Intent in the Discord Developer Portal.
- Check `DISCORD_ALLOWED_CHANNEL_IDS`. For normal `!` commands in a general text channel, add that text channel ID directly.
- In non-mapped channels, mention the bridge user if `DISCORD_PLAIN_ASK_MENTION_USER_IDS` is set.

`macOS cannot click or focus Codex`

- Grant Accessibility permission to the terminal app running `codex-discord-bot.sh`.
- Keep Codex Desktop installed, signed in, awake, and visible at least once.

`Windows desktop control cannot find Codex`

- Run `.\install.ps1 -DryRun`.
- Set `CODEX_DESKTOP_EXE` in `.env` if discovery cannot find the app.
- Run `.\codex-discord-bot.cmd` from the repo root.

## More Documentation

- [End-to-end architecture route map](docs/architecture-route-map.md)
- [Bundled skills and attribution](docs/plugin-skills.md)
- [ChatGPT Pro local-project MCP](docs/remote-mcp.md)
- [Daily workflow and Discord commands](docs/operations.md)
- [Steering, transport, validation, and project position](docs/policies-validation.md)
