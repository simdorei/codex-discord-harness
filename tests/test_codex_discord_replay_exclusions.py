from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from typing import cast
import unittest

import codex_discord_bot as bot
import codex_discord_message_dispatch as message_dispatch
import codex_discord_message_dispatch_runtime as dispatch_runtime
import codex_discord_message_gate as message_gate


@dataclass(frozen=True, slots=True)
class _Author:
    id: int = 7
    bot: bool = False


@dataclass(frozen=True, slots=True)
class _User:
    id: int = 99
    bot: bool = True


@dataclass(frozen=True, slots=True)
class _Channel:
    id: int = 222
    parent_id: int | None = None

    async def send(self, _text: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _Message:
    id: int
    content: str
    attachments: tuple[object, ...]
    author: _Author = _Author()
    channel: _Channel = _Channel()
    raw_mentions: tuple[int, ...] = ()
    mentions: tuple[object, ...] = ()


class _Owner:
    def __init__(self) -> None:
        self._processed_message_ids: dict[int, None] = {}
        self.user: _User = _User()
        self.plain_ask_mention_user_ids: set[int] = set()


class ReplayExclusionTests(unittest.IsolatedAsyncioTestCase):
    _old_db_path: Path = Path()
    _old_log_path: str | None = None
    _temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def setUp(self) -> None:
        self._old_db_path = bot.MIRROR_DB_PATH
        self._old_log_path = os.environ.get("CODEX_DISCORD_LOG_PATH")
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._temp_dir = temp_dir
        root = Path(temp_dir.name)
        bot.MIRROR_DB_PATH = root / "mirror.sqlite"
        os.environ["CODEX_DISCORD_LOG_PATH"] = str(root / "discord.log")
        bot.init_mirror_db()

    def tearDown(self) -> None:
        bot.MIRROR_DB_PATH = self._old_db_path
        if self._old_log_path is None:
            _ = os.environ.pop("CODEX_DISCORD_LOG_PATH", None)
        else:
            os.environ["CODEX_DISCORD_LOG_PATH"] = self._old_log_path
        if self._temp_dir is not None:
            self._temp_dir.cleanup()

    async def test_excluded_failures_are_persistently_non_replayable(self) -> None:
        cases = (
            (101, "command", "!boom", (), False, ValueError),
            (102, "attachment", "inspect", (object(),), False, RuntimeError),
            (103, "bridge", "bridge packet", (), True, RuntimeError),
            (104, "command_cancelled", "!cancel", (), False, asyncio.CancelledError),
            (105, "attachment_cancelled", "inspect", (object(),), False, asyncio.CancelledError),
        )
        for message_id, category, content, attachments, bridge_mention, error_type in cases:
            with self.subTest(category=category):
                owner = _Owner()
                message = _Message(message_id, content, attachments)

                async def fail_prefix(
                    message: message_dispatch.DispatchMessage,
                    command: str,
                ) -> None:
                    _ = message, command
                    raise error_type(f"{category} failed")

                async def fail_plain(
                    message: message_dispatch.DispatchMessage,
                    content: str,
                    *,
                    target_thread_id: str | None = None,
                    replay_eligible: bool = False,
                ) -> None:
                    _ = message, content, target_thread_id
                    self.assertFalse(replay_eligible)
                    raise error_type(f"{category} failed")

                async def process(
                    message: _Message,
                    *,
                    source: str,
                ) -> None:
                    await dispatch_runtime.process_inbound_discord_message_safely(
                        message,
                        source=source,
                        enable_prefix_commands=True,
                        deps=dispatch_runtime.InboundMessageRuntimeDeps(
                            require_messageable_channel=lambda channel: channel,
                            is_allowed_message_channel=lambda channel: True,
                            is_bot_authored_bridge_mention=lambda message: bridge_mention,
                            is_allowed_user=lambda user_id: True,
                            is_stopping=lambda: False,
                            send_restarting_notice=self._noop_notice,
                            get_mirrored_codex_thread_id=lambda channel_id: "thread-1",
                            get_bridge_mention_user_ids=set,
                            maybe_send_empty_content_notice=self._noop_empty,
                            prepare_plain_ask_message_content=self._prepare_content,
                            persist_inbound_mirror_thread_channel=lambda target_thread_id, channel_id: None,
                            handle_prefix_command=fail_prefix,
                            describe_mirrored_project_channel=lambda channel_id: None,
                            send_chunks=self._send_chunks,
                            handle_plain_ask=fail_plain,
                            format_log_text_len=len,
                            delivery_rejected_type=LookupError,
                            delivery_exceptions=(RuntimeError,),
                            format_exception=lambda: f"{category} failed",
                            log=lambda message: None,
                        ),
                    )

                async def process_gateway() -> None:
                    await message_gate.process_gateway_message(
                        message,
                        deps=message_gate.GatewayMessageDeps(
                            discord_client=owner,
                            claim_message=lambda value: bot.claim_gateway_discord_message(owner, value),
                            get_message_id=bot.PROCESSED_MESSAGE_RUNTIME.get_discord_message_id,
                            process_message=process,
                            release_message=lambda value: bot.release_gateway_discord_message(owner, value),
                            mark_processed=lambda value: bot.mark_discord_message_processed(owner, value),
                            log=lambda message: None,
                        ),
                    )

                if error_type in (ValueError, asyncio.CancelledError):
                    with self.assertRaises(error_type):
                        await process_gateway()
                else:
                    await process_gateway()

                restarted_owner = _Owner()
                self.assertTrue(
                    bot.discord_store.is_processed_discord_message_id(
                        bot.MIRROR_DB_PATH,
                        message_id,
                    )
                )
                self.assertFalse(
                    bot.claim_gateway_discord_message(restarted_owner, message)
                )

    async def test_required_mention_normalized_command_is_persistently_no_replay(self) -> None:
        owner = _Owner()
        owner.plain_ask_mention_user_ids = {99}
        message = _Message(
            106,
            "<@99> !boom",
            (),
            raw_mentions=(99,),
        )

        async def fail_prefix(
            message: message_dispatch.DispatchMessage,
            command: str,
        ) -> None:
            _ = message
            self.assertEqual(command, "boom")
            raise ValueError("normalized command failed")

        async def fail_plain(
            message: message_dispatch.DispatchMessage,
            content: str,
            *,
            target_thread_id: str | None = None,
            replay_eligible: bool = False,
        ) -> None:
            _ = message, content, target_thread_id, replay_eligible
            raise AssertionError("normalized command must not run as a plain ask")

        async def prepare_required_mention(
            message: message_dispatch.InboundMessage,
            content: str,
            target_thread_id: str | None,
            *,
            has_attachments: bool,
        ) -> str | None:
            result = message_gate.prepare_plain_ask_content(
                cast(message_gate.MessageWithMentions, cast(object, message)),
                content,
                {99},
                target_thread_id,
                has_attachments=has_attachments,
            )
            self.assertIs(result.action, message_gate.PlainAskGateAction.ACCEPT)
            return result.content

        async def process(message: _Message, *, source: str) -> None:
            await dispatch_runtime.process_inbound_discord_message_safely(
                message,
                source=source,
                enable_prefix_commands=True,
                deps=dispatch_runtime.InboundMessageRuntimeDeps(
                    require_messageable_channel=lambda channel: channel,
                    is_allowed_message_channel=lambda channel: True,
                    is_bot_authored_bridge_mention=lambda message: False,
                    is_allowed_user=lambda user_id: True,
                    is_stopping=lambda: False,
                    send_restarting_notice=self._noop_notice,
                    get_mirrored_codex_thread_id=lambda channel_id: "thread-1",
                    get_bridge_mention_user_ids=lambda: {99},
                    maybe_send_empty_content_notice=self._noop_empty,
                    prepare_plain_ask_message_content=prepare_required_mention,
                    persist_inbound_mirror_thread_channel=lambda target_thread_id, channel_id: None,
                    handle_prefix_command=fail_prefix,
                    describe_mirrored_project_channel=lambda channel_id: None,
                    send_chunks=self._send_chunks,
                    handle_plain_ask=fail_plain,
                    format_log_text_len=len,
                    delivery_rejected_type=LookupError,
                    delivery_exceptions=(RuntimeError,),
                    format_exception=lambda: "normalized command failed",
                    log=lambda message: None,
                ),
            )

        with self.assertRaisesRegex(ValueError, "normalized command failed"):
            await message_gate.process_gateway_message(
                message,
                deps=message_gate.GatewayMessageDeps(
                    discord_client=owner,
                    claim_message=lambda value: bot.claim_gateway_discord_message(owner, value),
                    get_message_id=bot.PROCESSED_MESSAGE_RUNTIME.get_discord_message_id,
                    process_message=process,
                    release_message=lambda value: bot.release_gateway_discord_message(owner, value),
                    mark_processed=lambda value: bot.mark_discord_message_processed(owner, value),
                    log=lambda message: None,
                ),
            )

        self.assertTrue(
            bot.discord_store.is_processed_discord_message_id(bot.MIRROR_DB_PATH, 106)
        )
        self.assertFalse(bot.claim_gateway_discord_message(_Owner(), message))

    async def test_error_report_cancellation_keeps_no_replay_ownership(self) -> None:
        owner = _Owner()
        message = _Message(107, "!boom", ())

        async def fail_prefix(
            message: message_dispatch.DispatchMessage,
            command: str,
        ) -> None:
            _ = message, command
            raise RuntimeError("configured command failure")

        async def cancel_error_report(
            target: message_dispatch.DispatchChannel,
            text: str,
        ) -> int:
            _ = target, text
            raise asyncio.CancelledError("error report cancelled")

        async def process(message: _Message, *, source: str) -> None:
            await dispatch_runtime.process_inbound_discord_message_safely(
                message,
                source=source,
                enable_prefix_commands=True,
                deps=dispatch_runtime.InboundMessageRuntimeDeps(
                    require_messageable_channel=lambda channel: channel,
                    is_allowed_message_channel=lambda channel: True,
                    is_bot_authored_bridge_mention=lambda message: False,
                    is_allowed_user=lambda user_id: True,
                    is_stopping=lambda: False,
                    send_restarting_notice=self._noop_notice,
                    get_mirrored_codex_thread_id=lambda channel_id: "thread-1",
                    get_bridge_mention_user_ids=set,
                    maybe_send_empty_content_notice=self._noop_empty,
                    prepare_plain_ask_message_content=self._prepare_content,
                    persist_inbound_mirror_thread_channel=lambda target_thread_id, channel_id: None,
                    handle_prefix_command=fail_prefix,
                    describe_mirrored_project_channel=lambda channel_id: None,
                    send_chunks=cancel_error_report,
                    handle_plain_ask=lambda *args, **kwargs: self._never_plain_ask(),
                    format_log_text_len=len,
                    delivery_rejected_type=LookupError,
                    delivery_exceptions=(RuntimeError,),
                    format_exception=lambda: "configured command failure",
                    log=lambda message: None,
                ),
            )

        with self.assertRaisesRegex(asyncio.CancelledError, "error report cancelled"):
            await message_gate.process_gateway_message(
                message,
                deps=message_gate.GatewayMessageDeps(
                    discord_client=owner,
                    claim_message=lambda value: bot.claim_gateway_discord_message(owner, value),
                    get_message_id=bot.PROCESSED_MESSAGE_RUNTIME.get_discord_message_id,
                    process_message=process,
                    release_message=lambda value: bot.release_gateway_discord_message(owner, value),
                    mark_processed=lambda value: bot.mark_discord_message_processed(owner, value),
                    log=lambda message: None,
                ),
            )

        self.assertTrue(
            bot.discord_store.is_processed_discord_message_id(bot.MIRROR_DB_PATH, 107)
        )
        self.assertFalse(bot.claim_gateway_discord_message(_Owner(), message))

    async def _noop_notice(
        self,
        target: message_dispatch.DispatchChannel,
    ) -> None:
        _ = target
        return None

    async def _noop_empty(self, message: message_dispatch.InboundMessage) -> None:
        _ = message
        return None

    async def _prepare_content(
        self,
        message: message_dispatch.InboundMessage,
        content: str,
        target_thread_id: str | None,
        *,
        has_attachments: bool,
    ) -> str:
        _ = message, target_thread_id, has_attachments
        return content

    async def _send_chunks(
        self,
        target: message_dispatch.DispatchChannel,
        text: str,
    ) -> int:
        _ = target, text
        return 1

    async def _never_plain_ask(self) -> None:
        raise AssertionError("plain ask must not run for a prefix command")


if __name__ == "__main__":
    _ = unittest.main()
