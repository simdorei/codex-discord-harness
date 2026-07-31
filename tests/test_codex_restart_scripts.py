import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_RESTART = ROOT / "plugins" / "codex-discord-remote" / "scripts" / "restart.ps1"
PLUGIN_STATUS = ROOT / "plugins" / "codex-discord-remote" / "scripts" / "status.ps1"
WATCHDOG = ROOT / "codex-discord-watchdog.ps1"
WATCHDOG_RUNTIME = ROOT / "codex-discord-watchdog-runtime.ps1"
WATCHDOG_RESTART_RUNTIME = ROOT / "codex-discord-watchdog-restart-runtime.ps1"


def _write_fake_restart_repo(
    repo_root: Path, bridge_line: str, bridge_stderr: str = ""
) -> None:
    _ = (repo_root / "codex-discord-watchdog.ps1").write_text(
        WATCHDOG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _ = (repo_root / "codex-discord-watchdog-runtime.ps1").write_text(
        WATCHDOG_RUNTIME.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _ = (repo_root / "codex-discord-watchdog-restart-runtime.ps1").write_text(
        WATCHDOG_RESTART_RUNTIME.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _ = (repo_root / "codex_discord_bot.py").write_text("", encoding="utf-8")
    bridge_script = "import sys\n\n"
    if bridge_stderr:
        bridge_script += f"print({bridge_stderr!r}, file=sys.stderr)\n"
    bridge_script += f"print({bridge_line!r})\n"
    _ = (repo_root / "codex_desktop_bridge.py").write_text(
        bridge_script, encoding="utf-8"
    )


def _watchdog_text() -> str:
    return "\n".join(
        [
            WATCHDOG.read_text(encoding="utf-8"),
            WATCHDOG_RUNTIME.read_text(encoding="utf-8"),
            WATCHDOG_RESTART_RUNTIME.read_text(encoding="utf-8"),
        ]
    )


@unittest.skipUnless(os.name == "nt", "PowerShell restart script tests run on Windows")
@unittest.skipUnless(shutil.which("powershell.exe"), "powershell.exe is required")
@unittest.skipUnless(shutil.which("py"), "py launcher is required")
class RestartScriptTests(unittest.TestCase):
    def run_restart_dry_run(
        self, repo_root: Path, quiet_seconds: int = 90
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PLUGIN_RESTART),
                "-RepoRoot",
                str(repo_root),
                "-DryRun",
                "-QuietSeconds",
                str(quiet_seconds),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def test_restart_dry_run_allows_old_idle_thread_activity(self) -> None:
        line = (
            "  1 | project:1 | idle | ctx 1/2 | used 1 | rec - | "
            "model gpt-5.5/xhigh/default/fast | thread-id | 2000-01-01 00:00:00 | old"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            _write_fake_restart_repo(repo_root, line)

            completed = self.run_restart_dry_run(repo_root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("restart_check_ok", completed.stdout)

    def test_restart_dry_run_allows_recent_idle_thread_activity(self) -> None:
        line = (
            "  1 | project:1 | idle | ctx 1/2 | used 1 | rec - | "
            "model gpt-5.5/xhigh/default/fast | thread-id | 2099-01-01 00:00:00 | active"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            _write_fake_restart_repo(repo_root, line)

            completed = self.run_restart_dry_run(repo_root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("restart_check_ok", completed.stdout)

    def test_restart_dry_run_allows_bridge_repair_notice_on_stderr(self) -> None:
        line = (
            "  1 | project:1 | idle | ctx 1/2 | used 1 | rec - | "
            "model gpt-5.5/xhigh/default/fast | thread-id | 2000-01-01 00:00:00 | active"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            _write_fake_restart_repo(
                repo_root,
                line,
                bridge_stderr="bridge_state_repaired: backup=state.json.corrupt.bak",
            )

            completed = self.run_restart_dry_run(repo_root)

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertIn("restart_check_ok", output)

    def test_restart_dry_run_rejects_busy_thread(self) -> None:
        line = (
            "  1 | project:1 | busy | ctx 1/2 | used 1 | rec - | "
            "model gpt-5.5/xhigh/default/fast | thread-id | 2000-01-01 00:00:00 | active"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            _write_fake_restart_repo(repo_root, line)

            completed = self.run_restart_dry_run(repo_root)

        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("busy", output)
        self.assertIn("Codex threads are busy", output)

    def test_restart_script_defers_watchdog_in_hidden_process_by_default(self) -> None:
        text = PLUGIN_RESTART.read_text(encoding="utf-8")

        self.assertIn("[switch]$Immediate", text)
        self.assertIn("restart_deferred:", text)
        self.assertIn("Start-Process -FilePath 'powershell.exe'", text)
        self.assertIn("-WindowStyle Hidden", text)
        self.assertIn("-RestartQuietSeconds $QuietSeconds", text)
        self.assertIn("-RestartWaitTimeoutSeconds $WaitTimeoutSeconds", text)

    def test_watchdog_waits_for_quiet_threads_before_restart_stop(self) -> None:
        text = _watchdog_text()

        self.assertIn("[int]$RestartQuietSeconds = 90", text)
        self.assertIn("function Wait-CodexThreadsQuietForRestart", text)
        self.assertIn("watchdog_restart_waiting", text)
        self.assertIn("Wait-CodexThreadsQuietForRestart", text)
        self.assertIn("Stop-RuntimeBotProcess", text)

    def test_watchdog_claims_restart_marker_before_waiting(self) -> None:
        text = _watchdog_text()
        claim_index = text.index("$claimedRestartPath = Claim-RestartRequest")
        wait_index = text.index("Wait-CodexThreadsQuietForRestart", claim_index)

        self.assertLess(claim_index, wait_index)
        self.assertIn(".codex_discord_bot.restart.claimed.$PID", text)
        self.assertIn("Move-Item -LiteralPath $RestartRequestPath", text)
        self.assertIn("watchdog_restart_claimed", text)
        self.assertIn("watchdog_restart_claim_lost", text)

    def test_watchdog_restores_orphaned_restart_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            restart_path = repo_root / ".codex_discord_bot.restart"
            claim_path = repo_root / ".codex_discord_bot.restart.claimed.999999"
            _ = claim_path.write_text("", encoding="ascii")
            command = "\n".join(
                [
                    f"$RestartRequestPath = {str(restart_path)!r}",
                    f"$RestartClaimPattern = {str(repo_root / '.codex_discord_bot.restart.claimed.*')!r}",
                    "function Write-LauncherLog { param([string]$Message) }",
                    f". {str(WATCHDOG_RESTART_RUNTIME)!r}",
                    "Restore-OrphanedRestartClaims",
                ]
            )
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(restart_path.exists())
            self.assertFalse(claim_path.exists())

    def test_tray_restart_reenables_a_disabled_scheduled_task(self) -> None:
        text = (ROOT / "codex-discord-tray.ps1").read_text(encoding="utf-8")

        self.assertIn("[System.IO.File]::WriteAllText($RestartRequestPath, '')", text)
        self.assertIn("Enable-ScheduledTask -TaskName 'Codex Discord Bot'", text)

    def test_watchdog_restarts_after_repeated_unhealthy_system_samples(self) -> None:
        text = _watchdog_text()

        self.assertIn("[int]$HealthCpuPercent = 95", text)
        self.assertIn("[int]$HealthFreeMemoryMb = 768", text)
        self.assertIn("[int]$HealthBadSampleLimit = 2", text)
        self.assertIn(".codex_discord_bot.health", text)
        self.assertIn("function Get-WatchdogSystemHealthIssue", text)
        self.assertIn("watchdog_unhealthy_sample", text)
        self.assertIn("watchdog_restart_unhealthy", text)
        self.assertIn("Stop-RuntimeBotProcess", text)

    def test_status_reports_codex_app_package_update_detection(self) -> None:
        text = PLUGIN_STATUS.read_text(encoding="utf-8")

        self.assertIn("Get-AppxPackage -Name OpenAI.Codex", text)
        self.assertIn("[System.IO.File]::ReadAllText", text)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", text)
        self.assertIn("[System.IO.File]::WriteAllText", text)
        self.assertIn("codex_app_package_version:", text)
        self.assertIn("codex_app_update_detected:", text)
        self.assertIn("codex_app_restart_recommended:", text)

    def test_status_preserves_utf8_state_when_recording_codex_app_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            repo_root.mkdir()
            state_path = Path(temp_dir) / "bridge_state.json"
            non_ascii_note = "\ud55c\uae00"
            _ = state_path.write_text(
                json.dumps(
                    {
                        "note": non_ascii_note,
                        "codex_app_package_version": "1.0.0.0",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            command = "\n".join(
                [
                    "function Get-AppxPackage {",
                    "    [CmdletBinding()]",
                    "    param([string]$Name)",
                    "    [pscustomobject]@{ Version = '2.0.0.0' }",
                    "}",
                    f"& {str(PLUGIN_STATUS)!r} -RepoRoot {str(repo_root)!r}",
                ]
            )
            env = os.environ.copy()
            env["CODEX_BRIDGE_STATE"] = str(state_path)

            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "codex_app_previous_package_version: 1.0.0.0", completed.stdout
            )
            self.assertIn("codex_app_update_detected: True", completed.stdout)
            state_bytes = state_path.read_bytes()
            self.assertFalse(state_bytes.startswith(b"\xef\xbb\xbf"))
            state_text = state_bytes.decode("utf-8")
            self.assertIn(non_ascii_note, state_text)
            self.assertIn('"codex_app_package_version"', state_text)
            self.assertIn('"2.0.0.0"', state_text)


if __name__ == "__main__":
    _ = unittest.main()
