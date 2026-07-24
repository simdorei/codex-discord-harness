from __future__ import annotations

from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import Protocol, cast
import tempfile
import unittest

import codex_discord_bot as bot

from tests.test_codex_discord_bot import EnvPatch, FakeInteraction, FakeTarget
from tests.test_codex_discord_busy_choice_steer_callback_integration import BusyMessage


class QueueButton(Protocol):
    label: str

    async def callback(self, interaction: FakeInteraction) -> None:
        ...


class BusyChoiceQueueViewWithChildren(Protocol):
    children: Sequence[QueueButton]


QueueCall = tuple[FakeTarget, str, str | None, bool, bool, BusyMessage | None]


class RunPromptFlow(Protocol):
    def __call__(
        self,
        channel: FakeTarget,
        prompt: str,
        *,
        queued: bool = False,
        source_message: BusyMessage | None = None,
        target_thread_id: str | None = None,
    ) -> Awaitable[None]:
        ...


def find_queue_button(view: BusyChoiceQueueViewWithChildren) -> QueueButton:
    return next(item for item in view.children if item.label == "Queue next")


class DiscordBusyChoiceQueueViewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_app_server_transport_enabled = bot.app_server_transport_enabled
        bot.app_server_transport_enabled = lambda: False

    def tearDown(self) -> None:
        bot.app_server_transport_enabled = self._original_app_server_transport_enabled

    async def test_queue_next_immediate_uses_runner_queue(self) -> None:
        original_get_busy_state = bot.get_busy_state_for_thread
        original_is_thread_runner_busy = bot.is_thread_runner_busy
        original_enqueue_thread_ask = bot.enqueue_thread_ask
        original_run_prompt_flow = cast(RunPromptFlow, bot.run_prompt_flow)
        calls: list[QueueCall] = []
        try:
            def get_idle_busy_state(target_thread_id: str | None) -> tuple[str, str | None, str]:
                return "idle", target_thread_id, "taxlab:1"

            async def runner_idle(target_thread_id: str | None) -> bool:
                _ = target_thread_id
                return False

            async def fake_enqueue_thread_ask(
                channel: FakeTarget,
                prompt: str,
                target_thread_id: str | None,
                *,
                queued: bool = False,
                ack_sent: bool = False,
                source_message: BusyMessage | None = None,
            ) -> int:
                calls.append((channel, prompt, target_thread_id, queued, ack_sent, source_message))
                return 1

            async def fail_run_prompt_flow(
                channel: FakeTarget,
                prompt: str,
                *,
                queued: bool = False,
                source_message: BusyMessage | None = None,
                target_thread_id: str | None = None,
            ) -> None:
                _ = channel, prompt, queued, source_message, target_thread_id
                raise AssertionError("queue_next immediate should use enqueue_thread_ask")

            bot.get_busy_state_for_thread = get_idle_busy_state
            bot.is_thread_runner_busy = runner_idle
            bot.enqueue_thread_ask = fake_enqueue_thread_ask
            bot.run_prompt_flow = fail_run_prompt_flow

            message = BusyMessage()
            view = cast(
                BusyChoiceQueueViewWithChildren,
                bot.BusyChoiceView(message, "please queue", target_thread_id="thread-1"),
            )
            button = find_queue_button(view)
            interaction = FakeInteraction(command_name="ask", channel_id=222)

            with tempfile.TemporaryDirectory() as temp_dir:
                log_path = Path(temp_dir) / "discord-smoke.log"
                with EnvPatch("CODEX_DISCORD_LOG_PATH", str(log_path)):
                    await button.callback(interaction)
                log_text = log_path.read_text(encoding="utf-8")

            self.assertEqual(
                calls,
                [(message.channel, "please queue", "thread-1", False, False, message)],
            )
            self.assertEqual(interaction.response.defer_kwargs, [{"thinking": False}])
            self.assertEqual(interaction.message.edits, [None])
            self.assertEqual(interaction.followup.messages, [])
            self.assertIn("queue_next_immediate user=242286902982606848", log_text)
            self.assertIn("component_message_components_cleared context=busy_choice_queue", log_text)
            self.assertIn("prompt_len=12", log_text)
            self.assertNotIn("prompt=please queue", log_text)
            self.assertIn("queue_next_immediate_enqueued user=242286902982606848", log_text)
        finally:
            bot.get_busy_state_for_thread = original_get_busy_state
            bot.is_thread_runner_busy = original_is_thread_runner_busy
            bot.enqueue_thread_ask = original_enqueue_thread_ask
            bot.run_prompt_flow = original_run_prompt_flow


if __name__ == "__main__":
    _ = unittest.main()
