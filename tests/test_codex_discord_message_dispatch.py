from __future__ import annotations

import unittest
from dataclasses import dataclass

import codex_discord_message_dispatch as message_dispatch


@dataclass(frozen=True, slots=True)
class FakeAuthor:
    id: int


@dataclass(frozen=True, slots=True)
class FakeChannel:
    id: int
    parent_id: int | None = None


@dataclass(frozen=True, slots=True)
class FakeMessage:
    author: FakeAuthor
    channel: FakeChannel
    content: str
    attachments: tuple[object, ...] = ()
    raw_mentions: tuple[int, ...] = ()
    mentions: tuple[object, ...] = ()


class InboundDiscordMessageDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_gateway_and_watermark_eligible_history_plain_text_are_replayable(self) -> None:
        replay_flags: list[bool] = []

        async def handle_plain_ask(
            message: message_dispatch.DispatchMessage,
            content: str,
            *,
            target_thread_id: str | None = None,
            replay_eligible: bool = False,
        ) -> None:
            _ = message, content, target_thread_id
            replay_flags.append(replay_eligible)

        async def run(source: str, *, attachments: tuple[object, ...] = ()) -> None:
            message = FakeMessage(
                author=FakeAuthor(242286902982606848),
                channel=FakeChannel(222),
                content="plain request",
                attachments=attachments,
            )
            def is_allowed_message_channel(channel: message_dispatch.DispatchChannel) -> bool:
                _ = channel
                return True

            def is_bot_authored_bridge_mention(message: message_dispatch.InboundMessage) -> bool:
                _ = message
                return False

            async def send_restarting_notice(target: message_dispatch.DispatchChannel) -> None:
                _ = target

            async def maybe_send_empty_content_notice(message: message_dispatch.InboundMessage) -> None:
                _ = message

            async def prepare_plain_ask_message_content(
                message: message_dispatch.InboundMessage,
                content: str,
                target_thread_id: str | None,
                *,
                has_attachments: bool,
            ) -> str | None:
                _ = message, target_thread_id, has_attachments
                return content

            async def handle_prefix_command(
                message: message_dispatch.DispatchMessage,
                command: str,
            ) -> None:
                _ = message, command

            async def send_chunks(target: message_dispatch.DispatchChannel, text: str) -> int:
                _ = target, text
                return 1

            deps = message_dispatch.InboundDiscordMessageProcessDeps(
                require_messageable_channel=lambda channel: channel,
                is_allowed_message_channel=is_allowed_message_channel,
                is_bot_authored_bridge_mention=is_bot_authored_bridge_mention,
                is_allowed_user=lambda user_id: user_id > 0,
                is_stopping=lambda: False,
                send_restarting_notice=send_restarting_notice,
                get_mirrored_codex_thread_id=lambda channel_id: "thread-1" if channel_id else None,
                get_bridge_mention_user_ids=set,
                maybe_send_empty_content_notice=maybe_send_empty_content_notice,
                prepare_plain_ask_message_content=prepare_plain_ask_message_content,
                persist_inbound_mirror_thread_channel=lambda target_thread_id, channel_id: None,
                handle_prefix_command=handle_prefix_command,
                describe_mirrored_project_channel=lambda channel_id: None,
                send_chunks=send_chunks,
                handle_plain_ask=handle_plain_ask,
                format_log_text_len=len,
                log=lambda _text: None,
            )
            await message_dispatch.process_inbound_discord_message(
                message,
                source=source,
                enable_prefix_commands=True,
                deps=deps,
            )

        async def _unused() -> None:
            return None

        _ = _unused
        await run("gateway")
        await run("history_poll")
        await run("gateway", attachments=(object(),))

        self.assertEqual(replay_flags, [True, True, False])

    async def test_prefix_command_bypasses_mirror_target_lookup_failure(self) -> None:
        logs: list[str] = []
        handled_commands: list[str] = []
        message = FakeMessage(
            author=FakeAuthor(242286902982606848),
            channel=FakeChannel(1522796405209567325),
            content="!list",
        )

        def failing_target_lookup(_channel_id: int | None) -> str | None:
            msg = "target lookup should not run for prefix commands"
            raise AssertionError(msg)

        async def send_restarting_notice(target: message_dispatch.DispatchChannel) -> None:
            _ = target
            return None

        async def maybe_send_empty_content_notice(_message: message_dispatch.InboundMessage) -> None:
            return None

        async def prepare_plain_ask_message_content(
            message: message_dispatch.InboundMessage,
            content: str,
            target_thread_id: str | None,
            *,
            has_attachments: bool,
        ) -> str | None:
            _ = message, content, target_thread_id
            self.assertFalse(has_attachments)
            msg = "plain ask preparation should not run for prefix commands"
            raise AssertionError(msg)

        async def handle_prefix_command(
            message: message_dispatch.DispatchMessage,
            command: str,
        ) -> None:
            _ = message
            handled_commands.append(command)

        async def send_chunks(target: message_dispatch.DispatchChannel, text: str) -> int:
            _ = target, text
            return 1

        async def handle_plain_ask(
            message: message_dispatch.DispatchMessage,
            content: str,
            *,
            target_thread_id: str | None = None,
            replay_eligible: bool = False,
        ) -> None:
            _ = message, content, replay_eligible
            self.assertIsNone(target_thread_id)
            msg = "plain ask handler should not run for prefix commands"
            raise AssertionError(msg)

        deps = message_dispatch.InboundDiscordMessageProcessDeps(
            require_messageable_channel=lambda channel: channel,
            is_allowed_message_channel=lambda channel: True,
            is_bot_authored_bridge_mention=lambda message: False,
            is_allowed_user=lambda user_id: user_id == 242286902982606848,
            is_stopping=lambda: False,
            send_restarting_notice=send_restarting_notice,
            get_mirrored_codex_thread_id=failing_target_lookup,
            get_bridge_mention_user_ids=set,
            maybe_send_empty_content_notice=maybe_send_empty_content_notice,
            prepare_plain_ask_message_content=prepare_plain_ask_message_content,
            persist_inbound_mirror_thread_channel=lambda _target_thread_id, _channel_id: None,
            handle_prefix_command=handle_prefix_command,
            describe_mirrored_project_channel=lambda _channel_id: None,
            send_chunks=send_chunks,
            handle_plain_ask=handle_plain_ask,
            format_log_text_len=len,
            log=logs.append,
        )

        await message_dispatch.process_inbound_discord_message(
            message,
            source="test",
            enable_prefix_commands=True,
            deps=deps,
        )

        self.assertEqual(handled_commands, ["list"])
        self.assertTrue(any("prefix=True" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
