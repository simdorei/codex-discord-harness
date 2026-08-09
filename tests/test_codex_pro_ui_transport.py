from __future__ import annotations

from collections.abc import Callable
from threading import Lock
import unittest

import codex_app_server_transport_delivery as app_server_delivery
import codex_desktop_bridge as bridge
import codex_discord_prompt_transport as prompt_transport
import codex_discord_prompt_transport_factory as prompt_transport_factory
from codex_pro_prompt_contract import PRO_SKILL_CALL
from tests.test_codex_discord_prompt_transport_factory import (
    FactoryRelay,
    FakeSteeringResult,
    make_app_steering_result,
)


class ProAppServerFactoryTests(unittest.TestCase):
    def test_pro_no_wait_never_calls_desktop_bridge(self) -> None:
        bridge_stream_calls: list[list[str]] = []
        resident_calls: list[tuple[str, str | None]] = []

        def resident_prompt(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            resident_calls.append((prompt, target_thread_id))
            return 0, "resident app server"

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
            _ = on_line
            return 0, "desktop ui"

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
        )

        result = prompt_transport.run_transport_prompt_no_wait(
            PRO_SKILL_CALL,
            "thread-1",
            deps,
        )

        self.assertEqual(result, (0, "resident app server"))
        self.assertEqual(resident_calls, [(PRO_SKILL_CALL, "thread-1")])
        self.assertEqual(bridge_stream_calls, [])

    def test_pro_stream_never_calls_desktop_bridge(self) -> None:
        bridge_stream_calls: list[list[str]] = []
        start_calls: list[tuple[str, str | None]] = []

        def legacy_prompt(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            raise AssertionError(f"unexpected legacy prompt: {prompt}:{target_thread_id}")

        def start_turn(
            prompt: str,
            target_thread_id: str | None,
        ) -> app_server_delivery.AppServerDeliveryResult:
            start_calls.append((prompt, target_thread_id))
            return app_server_delivery.AppServerDeliveryResult(
                0,
                "resident app server stream",
                thread_id=target_thread_id,
                target_ref="project:1",
            )

        def watch(
            steering_result: FakeSteeringResult,
            relay: FactoryRelay,
        ) -> tuple[int, str]:
            _ = steering_result
            relay.finish()
            return 0, "watched"

        def bridge_stream(argv: list[str], on_line: Callable[[str], None]) -> tuple[int, str]:
            bridge_stream_calls.append(argv)
            _ = on_line
            return 0, "desktop ui"

        deps = prompt_transport_factory.make_prompt_transport_deps(
            bridge_module=bridge,
            app_server_transport_enabled=lambda: True,
            run_legacy_prompt_no_wait=legacy_prompt,
            make_steering_prompt_result=make_app_steering_result,
            run_watch_stream=watch,
            run_bridge_command_stream=bridge_stream,
            ui_fallback_lock=Lock(),
            log=lambda message: None,
            start_turn_no_wait=start_turn,
        )
        relay = FactoryRelay()

        result = prompt_transport.run_ask_stream(
            PRO_SKILL_CALL,
            relay,
            target_thread_id="thread-1",
            deps=deps,
        )

        self.assertEqual(result, (0, "resident app server stream"))
        self.assertTrue(relay.finished)
        self.assertEqual(start_calls, [(PRO_SKILL_CALL, "thread-1")])
        self.assertEqual(bridge_stream_calls, [])


if __name__ == "__main__":
    unittest.main()
