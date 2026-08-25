from __future__ import annotations

import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace

import discord

import codex_discord_bot as bot
import codex_discord_bot_shapes as discord_bot_shapes


class PlainAskAdmissionAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_replayable_gateway_ask_is_deferred_before_app_server_admission(self) -> None:
        discord_client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(discord_client.close)
        raw_channel = discord_client.get_partial_messageable(222)
        message = discord_bot_shapes.RuntimeBusyChoiceSourceMessage(
            author=discord_bot_shapes.RuntimeBusyChoiceAuthor(
                id=242286902982606848,
                bot=False,
            ),
            channel=raw_channel,
        )
        deferred: list[tuple[str, str]] = []

        async def defer_plain_ask(
            channel: object,
            prompt: str,
            target_thread_id: str,
            *,
            source_message: object,
        ) -> bool:
            _ = channel
            self.assertIs(source_message, message)
            deferred.append((prompt, target_thread_id))
            return True

        @asynccontextmanager
        async def admission_must_not_run(
            channel: object,
            source_message: object | None,
            expected_generation: int | None,
        ) -> AsyncGenerator[bool, None]:
            _ = channel, source_message, expected_generation
            self.fail("app-server admission ran before durable inbox ownership")
            yield False

        runtime = type(bot.PLAIN_ASK_RUNTIME)(
            replace(
                bot.PLAIN_ASK_RUNTIME.deps,
                get_interactive_state_for_thread=lambda _target: ("", "thread-1", "thread-1"),
                defer_plain_ask=defer_plain_ask,
                prompt_admission=admission_must_not_run,
            )
        )

        await runtime.handle_plain_ask(
            message,
            "keep me",
            replay_eligible=True,
        )

        self.assertEqual(deferred, [("keep me", "thread-1")])

    async def test_plain_ask_resolves_channel_before_prompt_admission(self) -> None:
        discord_client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(discord_client.close)
        raw_channel = discord_client.get_partial_messageable(222)
        message = discord_bot_shapes.RuntimeBusyChoiceSourceMessage(
            author=discord_bot_shapes.RuntimeBusyChoiceAuthor(
                id=242286902982606848,
                bot=False,
            ),
            channel=raw_channel,
        )
        resolved_channel = object()
        resolved: list[object] = []
        admitted: list[tuple[object, object | None, int | None]] = []

        def require_messageable_channel(channel: object) -> object:
            resolved.append(channel)
            return resolved_channel

        @asynccontextmanager
        async def reject_admission(
            channel: object,
            source_message: object | None,
            expected_generation: int | None,
        ) -> AsyncGenerator[bool, None]:
            admitted.append((channel, source_message, expected_generation))
            yield False

        runtime = type(bot.PLAIN_ASK_RUNTIME)(
            replace(
                bot.PLAIN_ASK_RUNTIME.deps,
                require_messageable_channel=require_messageable_channel,
                prompt_admission=reject_admission,
                get_interactive_state_for_thread=lambda _target: ("", "thread-1", "thread-1"),
            )
        )

        await runtime.handle_plain_ask(message, "must not start")

        self.assertEqual(resolved, [raw_channel])
        self.assertEqual(admitted, [(resolved_channel, message, None)])


if __name__ == "__main__":
    _ = unittest.main()
