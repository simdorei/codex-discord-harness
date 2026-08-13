from __future__ import annotations

from threading import Lock
from typing import cast, Protocol
import unittest
from unittest import mock

import codex_app_server_transport_delivery as app_server_delivery
import codex_discord_prompt_transport_factory as prompt_transport_factory
import codex_pro_browser_evidence as pro_browser_evidence


class ProCompletionDeps(Protocol):
    def complete_pro_browser_session(
        self,
        target_thread_id: str | None,
        turn_id: str | None,
    ) -> None: ...


class ProBrowserCompletionTests(unittest.TestCase):
    def _deps(self) -> ProCompletionDeps:
        bridge_module = cast(
            app_server_delivery.BridgeModule,
            cast(object, type("FakeBridge", (), {})()),
        )
        return cast(
            ProCompletionDeps,
            cast(
                object,
                prompt_transport_factory.make_prompt_transport_deps(
                    bridge_module=bridge_module,
                    app_server_transport_enabled=lambda: True,
                    run_legacy_prompt_no_wait=lambda _prompt, _target: (0, "legacy"),
                    make_steering_prompt_result=lambda result: result,
                    run_watch_stream=lambda _result, _relay: (0, "watched"),
                    run_bridge_command_stream=lambda _argv, _on_line: (0, "legacy stream"),
                    ui_fallback_lock=Lock(),
                    log=lambda _message: None,
                ),
            ),
        )

    def test_completion_requires_exact_available_receipt(self) -> None:
        deps = self._deps()
        with mock.patch.object(
            pro_browser_evidence,
            "require_available_evidence",
        ) as require_evidence:
            deps.complete_pro_browser_session("thread-1", "turn-1")

        require_evidence.assert_called_once_with("thread-1", "turn-1")

    def test_missing_turn_identity_fails_before_reading_evidence(self) -> None:
        deps = self._deps()
        with (
            mock.patch.object(
                pro_browser_evidence,
                "require_available_evidence",
            ) as require_evidence,
            self.assertRaisesRegex(RuntimeError, "no exact thread and turn identity"),
        ):
            deps.complete_pro_browser_session("thread-1", None)

        require_evidence.assert_not_called()


if __name__ == "__main__":
    _ = unittest.main()
