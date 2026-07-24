from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Protocol, cast, override
from unittest import mock

import codex_discord_bot as bot


class FakeAuthor:
    id: int = 242286902982606848
    bot: bool = False


class AppServerUnavailableError(RuntimeError):
    pass


class FakeMessage:
    def __init__(
        self,
        content: str,
        *,
        channel_id: int = 333,
        message_id: int | None = None,
        created_at: dt.datetime | None = None,
    ) -> None:
        self.id: int | None = message_id
        self.author: FakeAuthor = FakeAuthor()
        self.content: str = content
        self.raw_mentions: list[int] = []
        self.mentions: list[FakeAuthor] = []
        self.attachments: list[str] = []
        self.embeds: list[str] = []
        self.stickers: list[str] = []
        self.created_at: dt.datetime = created_at or dt.datetime.now(dt.timezone.utc)
        self.channel: FakeHistoryChannel = FakeHistoryChannel(channel_id)


class FakeHistoryChannel:
    def __init__(self, channel_id: int = 333) -> None:
        self.id: int = channel_id
        self.history_messages: list[FakeMessage] = []
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)

    def history(self, *, limit: int) -> AsyncIterator[FakeMessage]:
        async def iterator() -> AsyncIterator[FakeMessage]:
            for message in self.history_messages[:limit]:
                yield message

        return iterator()


class ProcessDiscordMessageFunc(Protocol):
    def __call__(
        self,
        client: bot.CodexDiscordBot,
        message: FakeMessage,
        *,
        source: str,
    ) -> Awaitable[None]: ...


class PollHistoryChannelFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, label: str, channel_id: int) -> Awaitable[None]: ...


class OnMessageFunc(Protocol):
    def __call__(self, client: bot.CodexDiscordBot, message: FakeMessage) -> Awaitable[None]: ...


PROCESS_DISCORD_MESSAGE = cast(ProcessDiscordMessageFunc, bot.CodexDiscordBot.process_discord_message)
POLL_HISTORY_CHANNEL = cast(PollHistoryChannelFunc, bot.CodexDiscordBot.poll_history_channel)
ON_MESSAGE = cast(OnMessageFunc, bot.CodexDiscordBot.on_message)


class FakePollClient:
    def __init__(self, channel: FakeHistoryChannel) -> None:
        self._processed_message_ids: dict[int, set[int]] = {}
        self._history_poll_primed_channels: set[int] = set()
        self.enable_prefix_commands: bool = True
        self.channel: FakeHistoryChannel = channel

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[FakeHistoryChannel, str]:
        _ = channel_id
        return self.channel, "test_cache"

    async def fetch_channel(self, channel_id: int) -> FakeHistoryChannel:
        _ = channel_id
        raise AssertionError("fetch not expected")

    def is_allowed_message_channel(self, message_channel: FakeHistoryChannel) -> bool:
        _ = message_channel
        return True

    def is_allowed_user(self, user_id: int) -> bool:
        _ = user_id
        return True

    async def process_discord_message(self, message: FakeMessage, *, source: str) -> None:
        await PROCESS_DISCORD_MESSAGE(cast(bot.CodexDiscordBot, self), message, source=source)


LogAction = Callable[[Path], Awaitable[None]]


class DiscordHistoryPollPrimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        old_mirror_db_path = bot.MIRROR_DB_PATH
        mirror_db_temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(mirror_db_temp_dir.cleanup)
        self.addCleanup(setattr, bot, "MIRROR_DB_PATH", old_mirror_db_path)
        bot.MIRROR_DB_PATH = Path(mirror_db_temp_dir.name) / "mirror.sqlite"
        bot.init_mirror_db()

    async def _run_with_log(self, action: LogAction) -> str:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            log_path = Path(temp_dir) / "discord-smoke.log"
            with mock.patch.dict(os.environ, {"CODEX_DISCORD_LOG_PATH": str(log_path)}):
                await action(log_path)
            return log_path.read_text(encoding="utf-8")

    async def test_history_poll_primes_then_processes_new_user_message_once(self) -> None:
        handled: list[tuple[str, str | None]] = []
        channel = FakeHistoryChannel()
        old_message = FakeMessage(
            "old",
            message_id=100,
            created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1),
        )
        new_message = FakeMessage(
            "please hook",
            message_id=101,
            created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
        )
        old_message.channel = channel
        new_message.channel = channel
        client = FakePollClient(channel)

        async def runner_idle(target_thread_id: str | None) -> bool:
            _ = target_thread_id
            return False

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = message
            handled.append((prompt, target_thread_id))

        def mirror_thread_id(channel_id: int) -> str:
            _ = channel_id
            return "thread-1"

        def busy_state(target_thread_id: str | None) -> tuple[str, None, str]:
            _ = target_thread_id
            return "idle", None, ""

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            channel.history_messages = [old_message]
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            channel.history_messages = [new_message, old_message]
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            await ON_MESSAGE(bot_client, new_message)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", mirror_thread_id),
            mock.patch.object(bot, "get_busy_state_for_thread", busy_state),
            mock.patch.object(bot, "is_thread_runner_busy", runner_idle),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [("please hook", "thread-1")])
        self.assertIn("history_poll_primed label=allowed channel=333", log_text)
        self.assertIn("history_poll_message channel=333", log_text)
        self.assertIn("message_received chat=333", log_text)
        self.assertIn("source=history_poll", log_text)
        self.assertIn("duplicate_message_skipped source=gateway chat=333 message=101", log_text)

    async def test_history_poll_first_prime_discards_offline_backlog(self) -> None:
        handled: list[tuple[str, str | None]] = []
        channel = FakeHistoryChannel()
        cutoff = dt.datetime(2026, 6, 3, 15, 0, tzinfo=dt.timezone.utc)
        old_message = FakeMessage("old", message_id=100, created_at=cutoff - dt.timedelta(seconds=1))
        fresh_message = FakeMessage("bootstrap hook", message_id=101, created_at=cutoff + dt.timedelta(seconds=1))
        old_message.channel = channel
        fresh_message.channel = channel
        channel.history_messages = [fresh_message, old_message]
        client = FakePollClient(channel)

        async def runner_idle(target_thread_id: str | None) -> bool:
            _ = target_thread_id
            return False

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = message
            handled.append((prompt, target_thread_id))

        def mirror_thread_id(channel_id: int) -> str:
            _ = channel_id
            return "thread-1"

        def busy_state(target_thread_id: str | None) -> tuple[str, None, str]:
            _ = target_thread_id
            return "idle", None, ""

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await POLL_HISTORY_CHANNEL(cast(bot.CodexDiscordBot, client), "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", mirror_thread_id),
            mock.patch.object(bot, "get_busy_state_for_thread", busy_state),
            mock.patch.object(bot, "is_thread_runner_busy", runner_idle),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [])
        self.assertIn("history_poll_primed label=allowed channel=333", log_text)
        self.assertIn("fetched_messages=2", log_text)
        self.assertIn("claimed_messages=2", log_text)
        self.assertIn("eligible_messages=0", log_text)
        self.assertIn("discarded_messages=2", log_text)
        self.assertNotIn("history_poll_message channel=333", log_text)
        self.assertNotIn("old", log_text)

    async def test_history_poll_watermark_blocks_old_message_after_processed_ids_are_lost(self) -> None:
        handled: list[str] = []
        channel = FakeHistoryChannel()
        old_message = FakeMessage(
            "old backlog",
            message_id=100,
            created_at=dt.datetime(2026, 6, 3, 15, 0, tzinfo=dt.timezone.utc),
        )
        old_message.channel = channel
        channel.history_messages = [old_message]
        client = FakePollClient(channel)

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = (message, target_thread_id)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            client._processed_message_ids.clear()
            bot.discord_store.cleanup_processed_discord_messages(
                bot.MIRROR_DB_PATH,
                retention_seconds=0,
                now=dt.datetime.now(dt.timezone.utc).timestamp() + 1,
            )
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [])
        self.assertEqual(log_text.count("history_poll_primed label=allowed channel=333"), 1)
        self.assertNotIn("history_poll_message channel=333", log_text)

    async def test_history_poll_accepts_message_created_after_empty_prime(self) -> None:
        handled: list[str] = []
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = (message, target_thread_id)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            new_message = FakeMessage(
                "new after start",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
            )
            new_message.channel = channel
            channel.history_messages = [new_message]
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            await self._run_with_log(run_poll)

        self.assertEqual(handled, ["new after start"])

    async def test_history_poll_empty_prime_still_blocks_old_message_seen_later(self) -> None:
        handled: list[str] = []
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)

        async def fake_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = (message, target_thread_id)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            delayed_old_message = FakeMessage(
                "delayed old backlog",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
            )
            delayed_old_message.channel = channel
            channel.history_messages = [delayed_old_message]
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [])
        self.assertNotIn("history_poll_message channel=333", log_text)

    async def test_history_poll_app_server_failure_is_not_replayed(self) -> None:
        attempts: list[str] = []
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)

        async def fail_handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
        ) -> None:
            _ = (message, target_thread_id)
            attempts.append(prompt)
            raise AppServerUnavailableError("app server unavailable")

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            bot_client = cast(bot.CodexDiscordBot, client)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            offline_message = FakeMessage(
                "while app server offline",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
            )
            offline_message.channel = channel
            channel.history_messages = [offline_message]
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)
            await POLL_HISTORY_CHANNEL(bot_client, "allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(bot, "handle_plain_ask", fail_handle_plain_ask),
        ):
            await self._run_with_log(run_poll)

        self.assertEqual(attempts, ["while app server offline"])


if __name__ == "__main__":
    _ = unittest.main()
