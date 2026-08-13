from __future__ import annotations

from collections.abc import Callable
from threading import Lock
import unittest

import codex_desktop_bridge as bridge
import codex_discord_prompt_transport as prompt_transport
import codex_discord_prompt_transport_factory as prompt_transport_factory
from codex_pro_prompt_contract import PRO_SKILL_CALL
from tests.test_codex_discord_prompt_transport_factory import (
    FactoryRelay,
    FakeSteeringResult,
    make_app_steering_result,
)


class ProDesktopIpcFactoryTests(unittest.TestCase):
    def test_pro_no_wait_uses_desktop_ipc_with_ui_recovery(self) -> None:
        bridge_stream_calls: list[list[str]] = []
        resident_calls: list[tuple[str, str | None]] = []
        completion_calls: list[tuple[str | None, str | None]] = []

        def resident_prompt(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            resident_calls.append((prompt, target_thread_id))
            return 0, "resident app server\n[app_server_delivery] turn_id=turn-1"

        def legacy_prompt(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            raise AssertionError(f"unexpected legacy prompt: {prompt}:{target_thread_id}")

        def watch(
            steering_result: FakeSteeringResult,
            relay: FactoryRelay,
        ) -> tuple[int, str]:
            _ = steering_result
            relay.finish()
            return 0, "watched"

        def bridge_stream(argv: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
            bridge_stream_calls.append(argv)
            on_line("[ipc_delivery] owner_client=client-1 turn_id=turn-1")
            return 0, "[ipc_delivery] owner_client=client-1 turn_id=turn-1"

        deps = prompt_transport_factory.make_prompt_transport_deps(
            bridge_module=bridge,
            app_server_transport_enabled=lambda: True,
            run_legacy_prompt_no_wait=legacy_prompt,
            make_steering_prompt_result=make_app_steering_result,
            run_watch_stream=watch,
            run_bridge_command_stream=bridge_stream,
            ui_fallback_lock=Lock(),
            log=lambda message: None,
            run_resident_prompt_no_wait=resident_prompt,
            complete_pro_browser_session=lambda target, turn: completion_calls.append(
                (target, turn)
            ),
        )

        result = prompt_transport.run_transport_prompt_no_wait(
            PRO_SKILL_CALL,
            "thread-1",
            deps,
        )

        self.assertEqual(
            result,
            (0, "[ipc_delivery] owner_client=client-1 turn_id=turn-1"),
        )
        self.assertEqual(resident_calls, [])
        self.assertEqual(completion_calls, [("thread-1", "turn-1")])
        self.assertEqual(len(bridge_stream_calls), 1)
        self.assertEqual(bridge_stream_calls[0][0:3], ["ask", "--ipc", "--ipc-recover-ui"])
        self.assertIn("--no-fallback", bridge_stream_calls[0])
        self.assertNotIn("--no-wait", bridge_stream_calls[0])

    def test_pro_stream_uses_desktop_ipc_with_ui_recovery(self) -> None:
        bridge_stream_calls: list[list[str]] = []
        completion_calls: list[tuple[str | None, str | None]] = []

        def legacy_prompt(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            raise AssertionError(f"unexpected legacy prompt: {prompt}:{target_thread_id}")

        def watch(
            steering_result: FakeSteeringResult,
            relay: FactoryRelay,
        ) -> tuple[int, str]:
            _ = steering_result
            relay.finish()
            return 0, "watched"

        def bridge_stream(argv: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
            bridge_stream_calls.append(argv)
            on_line("[ipc_delivery] owner_client=client-1 turn_id=turn-1")
            return 0, "[ipc_delivery] owner_client=client-1 turn_id=turn-1"

        deps = prompt_transport_factory.make_prompt_transport_deps(
            bridge_module=bridge,
            app_server_transport_enabled=lambda: True,
            run_legacy_prompt_no_wait=legacy_prompt,
            make_steering_prompt_result=make_app_steering_result,
            run_watch_stream=watch,
            run_bridge_command_stream=bridge_stream,
            ui_fallback_lock=Lock(),
            log=lambda message: None,
            complete_pro_browser_session=lambda target, turn: completion_calls.append(
                (target, turn)
            ),
        )
        relay = FactoryRelay()

        result = prompt_transport.run_ask_stream(
            PRO_SKILL_CALL,
            relay,
            target_thread_id="thread-1",
            deps=deps,
        )

        self.assertEqual(result, (0, "[ipc_delivery] owner_client=client-1 turn_id=turn-1"))
        self.assertTrue(relay.finished)
        self.assertEqual(relay.lines, ["[ipc_delivery] owner_client=client-1 turn_id=turn-1"])
        self.assertEqual(completion_calls, [("thread-1", "turn-1")])
        self.assertEqual(len(bridge_stream_calls), 1)
        self.assertEqual(bridge_stream_calls[0][0:3], ["ask", "--ipc", "--ipc-recover-ui"])


if __name__ == "__main__":
    _ = unittest.main()
