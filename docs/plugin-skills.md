## Bundled Skills And Attribution

The Codex Discord Remote plugin packages these skills. In Codex, the fully qualified
skill names use the `$codex-discord-remote:<skill-name>` form. In Discord,
some skills also have slash or `!` command wrappers.

| Skill | Purpose | Discord entrypoint |
| --- | --- | --- |
| `ask-chatgpt-pro` | Reuse one normal ChatGPT Pro conversation per Codex thread, bind it to the originating local project through OAuth-protected MCP, and optionally launch session-owned isolated Chrome or operate blank Notepad with short-lived screenshot tokens. Chrome pixels remain unavailable. A missing saved conversation is replaced. | `!pro <question>` invokes the skill from a mapped Discord thread. |
| `discord-remote` | Operational runbook for this local Discord bridge: bot status, watchdog restarts, archive-lock recovery, mirror routing checks, log triage, and local deployment checks. | No direct skill wrapper; use the normal remote commands such as `!status`, `!doctor`, `!mirror check`, and `!bridge sync`. |
| `discord-remote-qa` | Focused QA checklist for mirror mapping, context refresh, session mirror cursor priming, steering suppression, archive lock retry, and deployment readiness. | No direct skill wrapper; use it from Codex when validating remote changes. |
| `archive-used` | Bulk archive workflow for Codex threads whose `used` value in bridge list output is at or above a user-provided `<threshold>`, targeting the UUID printed in each selected list row. | `!archive-used <threshold>` invokes the skill from Discord; Codex then uses local bridge list/archive commands or the equivalent `!list` and `!archive <uuid-from-list>`. |
| `deep-interview` | Clarification-first requirements workflow. It confirms the work structure, asks one question at a time, scores ambiguity, preserves the user's language, tracks scope/entities/constraints, and stops at a pending-approval ticket before implementation. | `/interview <request>` or `!interview <request>`. |

Source attribution is included for transparency and license compliance. These
upstream authors are not listed as this repository's contributors unless they
contributed directly here; the notes below identify inspiration, adaptation, or
vendored source material only.

- `deep-interview` is adapted from the Gajae Code deep-interview skill:
  https://github.com/Yeachan-Heo/gajae-code/tree/main/packages/coding-agent/src/defaults/gjc/skills/deep-interview
- Gajae Code is MIT licensed. The packaged notice is at
  `plugins/codex-discord-remote/skills/deep-interview/NOTICE.md`.
- Repository-level third-party notices are collected in `NOTICE.md`.

Useful plugin-backed scripts can also be run directly from the repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-remote\scripts\status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-remote\scripts\restart.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\plugins\codex-discord-remote\scripts\qa-smoke.ps1 -SkipUnitTests
```

`restart.ps1` queues a deferred restart by default. The watchdog waits until every DB-root
Codex thread is `idle` and no listed thread has recent activity before stopping the bot, so
the current Discord-mirrored turn can finish. Use `-DryRun` to check readiness or `-Immediate`
when you intentionally want the script to wait in the foreground. Both restart modes enforce
at least 15 seconds of delay and 15 seconds of quiet time, and bind the stop request to the
exact running bot process so a replacement process cannot be stopped accidentally. Status and
restart checks choose Python in this order: explicit `CODEX_DISCORD_PYTHON`, process or repository
`.env` `PYTHON_EXE`, then `.python-portable\python.exe` as the fallback.
