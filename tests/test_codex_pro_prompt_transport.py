from __future__ import annotations

import unittest

import codex_discord_prompt_transport as prompt_transport
from tests.test_codex_discord_prompt_transport import (
    FakeDeliveryResult,
    FakeRelay,
    build_deps,
)


PRO_PROMPT = "$ask-chatgpt-pro [@Browser](plugin://browser@openai-bundled)"


class ProPromptTransportTests(unittest.TestCase):
    def test_pro_prompt_no_wait_activates_target_thread_before_resident_turn(self) -> None:
        events: list[str] = []

        def prepare(target_thread_id: str | None) -> None:
            events.append(f"activate:{target_thread_id}")

        def resident(_prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            events.append(f"resident:{target_thread_id}")
            return 0, "resident app server"

        exit_code, _ = prompt_transport.run_transport_prompt_no_wait(
            PRO_PROMPT,
            "thread-1",
            build_deps(
                enabled=False,
                prepare_pro_browser_session=prepare,
                run_resident_prompt_no_wait=resident,
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, ["activate:thread-1", "resident:thread-1"])

    def test_pro_stream_activates_target_thread_before_resident_turn(self) -> None:
        relay = FakeRelay()
        events: list[str] = []

        def prepare(target_thread_id: str | None) -> None:
            events.append(f"activate:{target_thread_id}")

        def start_turn(_prompt: str, target_thread_id: str | None) -> FakeDeliveryResult:
            events.append(f"resident:{target_thread_id}")
            return FakeDeliveryResult(0, "resident app server stream")

        exit_code, _ = prompt_transport.run_ask_stream(
            PRO_PROMPT,
            relay,
            wait=False,
            target_thread_id="thread-1",
            deps=build_deps(
                enabled=False,
                prepare_pro_browser_session=prepare,
                start_turn_no_wait=start_turn,
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, ["activate:thread-1", "resident:thread-1"])

    def test_pro_prompt_no_wait_uses_resident_even_when_legacy_is_enabled(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def resident(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            calls.append((prompt, target_thread_id))
            return 0, "resident app server"

        def legacy(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
            raise AssertionError(f"unexpected legacy transport: {prompt}:{target_thread_id}")

        exit_code, output = prompt_transport.run_transport_prompt_no_wait(
            PRO_PROMPT,
            "thread-1",
            build_deps(
                enabled=False,
                run_resident_prompt_no_wait=resident,
                run_legacy_prompt_no_wait=legacy,
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "resident app server")
        self.assertEqual(calls, [(PRO_PROMPT, "thread-1")])

    def test_pro_stream_uses_resident_even_when_legacy_is_enabled(self) -> None:
        relay = FakeRelay()
        calls: list[tuple[str, str | None]] = []

        def start_turn(prompt: str, target_thread_id: str | None) -> FakeDeliveryResult:
            calls.append((prompt, target_thread_id))
            return FakeDeliveryResult(0, "resident app server stream")

        def legacy_stream(
            prompt: str,
            relay: FakeRelay,
            *,
            force_while_busy: bool = False,
            wait: bool = True,
            target_thread_id: str | None = None,
        ) -> tuple[int, str]:
            raise AssertionError(
                f"unexpected legacy stream: {prompt}:{force_while_busy}:{wait}:{target_thread_id}:{relay}"
            )

        exit_code, output = prompt_transport.run_ask_stream(
            PRO_PROMPT,
            relay,
            force_while_busy=True,
            wait=False,
            target_thread_id="thread-1",
            deps=build_deps(
                enabled=False,
                start_turn_no_wait=start_turn,
                run_legacy_stream=legacy_stream,
            ),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "resident app server stream")
        self.assertTrue(relay.finished)
        self.assertEqual(calls, [(PRO_PROMPT, "thread-1")])


if __name__ == "__main__":
    _ = unittest.main()
