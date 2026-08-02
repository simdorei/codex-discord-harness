from __future__ import annotations

import json
import unittest
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import codex_discord_prompt_rewrite as prompt_rewrite
import codex_discord_prompt_mapped_delivery as mapped_delivery
import codex_pro_runtime_preflight as pro_preflight
from codex_app_server_transport_lifecycle import AppServerLifecycleSnapshot
from codex_remote_mcp_bridge_config import ProjectTicket
from codex_remote_mcp_bridge_connection import RemoteMcpBridgeError


REMOTE_PLUGIN = "codex-discord-remote@codex-discord-remote"
BROWSER_PLUGIN = "browser@openai-bundled"
EXPECTED_VERSION = "1.2.3"


def _inventory(
    *,
    remote_installed: bool = True,
    remote_enabled: bool = True,
    remote_version: str = EXPECTED_VERSION,
    browser_installed: bool = True,
    browser_enabled: bool = True,
) -> str:
    return json.dumps(
        {
            "installed": [
                {
                    "pluginId": REMOTE_PLUGIN,
                    "version": remote_version,
                    "installed": remote_installed,
                    "enabled": remote_enabled,
                },
                {
                    "pluginId": BROWSER_PLUGIN,
                    "version": "9.8.7",
                    "installed": browser_installed,
                    "enabled": browser_enabled,
                },
            ]
        }
    )


def _healthy_resident() -> AppServerLifecycleSnapshot:
    return AppServerLifecycleSnapshot(
        generation=7,
        healthy=True,
        accepting_since=1.0,
    )


def _missing_bridge(
    thread_id: str,
    project_scope: str,
    root: Path,
    log: prompt_rewrite.LogFunc,
) -> None:
    _ = thread_id, project_scope, root, log


def _expired_ticket(
    thread_id: str,
    project_scope: str,
    root: Path,
    log: prompt_rewrite.LogFunc,
) -> ProjectTicket:
    _ = thread_id, project_scope, root, log
    return ProjectTicket(
        project_scope="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )


def _stale_bridge(
    thread_id: str,
    project_scope: str,
    root: Path,
    log: prompt_rewrite.LogFunc,
) -> ProjectTicket:
    _ = thread_id, project_scope, root, log
    raise RemoteMcpBridgeError(
        "The local project bridge did not acknowledge the binding in time."
    )


class ProRuntimePreflightTests(unittest.TestCase):
    def test_accepts_enabled_plugins_and_healthy_resident(self) -> None:
        status = pro_preflight.verify_pro_runtime(
            inventory_json=_inventory(),
            expected_remote_version=EXPECTED_VERSION,
            resident_snapshot=_healthy_resident(),
        )

        self.assertEqual(status.remote_plugin_version, EXPECTED_VERSION)
        self.assertEqual(status.browser_plugin_version, "9.8.7")
        self.assertEqual(status.resident_generation, 7)

    def test_rejects_missing_disabled_or_stale_plugins(self) -> None:
        cases: tuple[tuple[str, str, str], ...] = (
            (
                "remote_missing",
                json.dumps({"installed": []}),
                "codex-discord-remote@codex-discord-remote.*exactly once",
            ),
            (
                "remote_disabled",
                _inventory(remote_enabled=False),
                "codex-discord-remote@codex-discord-remote.*not enabled",
            ),
            (
                "remote_stale",
                _inventory(remote_version="0.0.0-stale"),
                "version mismatch",
            ),
            (
                "browser_missing",
                _inventory(browser_installed=False),
                "browser@openai-bundled.*not installed",
            ),
            (
                "browser_disabled",
                _inventory(browser_enabled=False),
                "browser@openai-bundled.*not enabled",
            ),
        )
        for name, inventory_json, expected in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    pro_preflight.ProRuntimePreflightError,
                    expected,
                ):
                    _ = pro_preflight.verify_pro_runtime(
                        inventory_json=inventory_json,
                        expected_remote_version=EXPECTED_VERSION,
                        resident_snapshot=_healthy_resident(),
                    )

    def test_rejects_malformed_inventory_and_unhealthy_resident(self) -> None:
        with self.assertRaisesRegex(
            pro_preflight.ProRuntimePreflightError,
            "not valid JSON",
        ):
            _ = pro_preflight.verify_pro_runtime(
                inventory_json="{bad-json",
                expected_remote_version=EXPECTED_VERSION,
                resident_snapshot=_healthy_resident(),
            )

        with self.assertRaisesRegex(
            pro_preflight.ProRuntimePreflightError,
            "resident Codex app-server is not healthy",
        ):
            _ = pro_preflight.verify_pro_runtime(
                inventory_json=_inventory(),
                expected_remote_version=EXPECTED_VERSION,
                resident_snapshot=AppServerLifecycleSnapshot(
                    generation=7,
                    healthy=False,
                    accepting_since=None,
                    restart_pending=True,
                ),
            )


class ProPromptPreflightTests(unittest.TestCase):
    def _rewrite(
        self,
        *,
        checker: Callable[[], pro_preflight.ProRuntimeStatus],
        registrar: prompt_rewrite.ProjectRegistrar,
    ) -> mapped_delivery.PromptPreprocessResult:
        return prompt_rewrite.rewrite_prompt(
            "!pro inspect",
            target_thread_id="thread-1",
            cwd=Path.cwd(),
            log=lambda _: None,
            runtime_preflight=checker,
            project_registrar=registrar,
        )

    def test_preflight_failure_blocks_transport_with_exact_reason(self) -> None:
        def fail() -> pro_preflight.ProRuntimeStatus:
            raise pro_preflight.ProRuntimePreflightError(
                "plugin 'browser@openai-bundled' is not enabled"
            )

        result = self._rewrite(checker=fail, registrar=_missing_bridge)

        self.assertFalse(result.should_deliver)
        self.assertIn("browser@openai-bundled", result.visible_line)
        self.assertIn("not enabled", result.error_message)

    def test_missing_bridge_configuration_blocks_instead_of_falling_back(self) -> None:
        result = self._rewrite(
            checker=lambda: pro_preflight.ProRuntimeStatus(
                remote_plugin_version=EXPECTED_VERSION,
                browser_plugin_version="9.8.7",
                resident_generation=7,
            ),
            registrar=_missing_bridge,
        )

        self.assertFalse(result.should_deliver)
        self.assertIn("remote MCP is not configured", result.error_message)

    def test_expired_project_ticket_blocks_transport(self) -> None:
        result = self._rewrite(
            checker=lambda: pro_preflight.ProRuntimeStatus(
                remote_plugin_version=EXPECTED_VERSION,
                browser_plugin_version="9.8.7",
                resident_generation=7,
            ),
            registrar=_expired_ticket,
        )

        self.assertFalse(result.should_deliver)
        self.assertIn("already expired", result.error_message)

    def test_stale_bridge_blocks_transport_with_bridge_reason(self) -> None:
        result = self._rewrite(
            checker=lambda: pro_preflight.ProRuntimeStatus(
                remote_plugin_version=EXPECTED_VERSION,
                browser_plugin_version="9.8.7",
                resident_generation=7,
            ),
            registrar=_stale_bridge,
        )

        self.assertFalse(result.should_deliver)
        self.assertIn("did not acknowledge", result.error_message)


if __name__ == "__main__":
    _ = unittest.main()
