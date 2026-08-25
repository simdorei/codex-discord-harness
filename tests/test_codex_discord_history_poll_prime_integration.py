from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import cast, override
from unittest import mock

import codex_discord_bot as bot
import codex_discord_logging as discord_logging
import codex_discord_message_gate as discord_message_gate
import codex_discord_message_dispatch_runtime as message_dispatch_runtime
import codex_discord_diagnostics_history as discord_diagnostics_history
import codex_discord_history_poll as discord_history_poll
import codex_discord_runner_runtime as discord_runner_runtime
from codex_discord_durable_queue_runtime import DeferredIntakeResult
from codex_discord_seen_cache import SeenCacheMap
from codex_discord_store_schema import StoreSchemaVersionError


class FakeAuthor:
    id: int = 242286902982606848
    bot: bool = False


class FakeMessage:
    type: None = None

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


class FakePollClient:
    def __init__(self, channel: FakeHistoryChannel) -> None:
        self._processed_message_ids: SeenCacheMap = {}
        self._history_poll_primed_channels: set[int] = set()
        self.allowed_channel_ids: set[int] = {channel.id}
        self.startup_channel_id: int | None = None
        self.history_poll_seconds: float = 1.0
        self.enable_prefix_commands: bool = True
        self.plain_ask_mention_user_ids: list[int] = []
        self.user: None = None
        self.channel: FakeHistoryChannel = channel

    def is_closed(self) -> bool:
        return False

    def get_cached_channel_or_thread(self, channel_id: int) -> tuple[object | None, str]:
        _ = channel_id
        return self.channel, "test_cache"

    async def fetch_channel(self, channel_id: int) -> object | None:
        _ = channel_id
        raise AssertionError("fetch not expected")

    async def history_poll_loop(self) -> None:
        return

    async def poll_history_channel(self, label: str, channel_id: int) -> None:
        await bot.HISTORY_RUNTIME.poll_history_channel(self, label, channel_id)

    def is_allowed_message_channel(self, channel: object) -> bool:
        _ = channel
        return True

    def is_allowed_user(self, user_id: int | None) -> bool:
        _ = user_id
        return True

    async def process_discord_message(self, message: object, *, source: str) -> None:
        if not isinstance(message, FakeMessage):
            raise TypeError(f"expected FakeMessage, got {type(message).__name__}")
        await bot.MESSAGE_RUNTIME.process_discord_message(self, message, source=source)


class RecordingHistoryRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int]] = []

    async def poll_history_channel(self, owner: object, label: str, channel_id: int) -> None:
        self.calls.append((owner, label, channel_id))


class RecordingMessageRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, str]] = []

    async def process_discord_message(self, owner: object, message: object, *, source: str) -> None:
        self.calls.append((owner, message, source))


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

    async def test_dynamic_client_poll_and_message_methods_delegate_to_runtimes(self) -> None:
        history_runtime = RecordingHistoryRuntime()
        message_runtime = RecordingMessageRuntime()
        client = bot.CodexDiscordBot.__new__(bot.CodexDiscordBot)
        message = FakeMessage("delegated", message_id=777)

        with (
            mock.patch.object(bot, "HISTORY_RUNTIME", history_runtime),
            mock.patch.object(bot, "MESSAGE_RUNTIME", message_runtime),
        ):
            await client.poll_history_channel("allowed", 333)
            await client.process_discord_message(message, source="adapter-test")

        self.assertEqual(history_runtime.calls, [(client, "allowed", 333)])
        self.assertEqual(message_runtime.calls, [(client, message, "adapter-test")])

    async def test_history_processing_failure_releases_the_transient_message_claim(self) -> None:
        channel = FakeHistoryChannel()
        message = FakeMessage("retry me", message_id=777)
        message.channel = channel
        client = FakePollClient(channel)
        self.assertTrue(
            bot.PROCESSED_MESSAGE_RUNTIME.claim_gateway_discord_message(client, message)
        )

        with mock.patch.object(
            client,
            "process_discord_message",
            mock.AsyncMock(side_effect=RuntimeError("dispatch interrupted")),
        ):
            with self.assertRaisesRegex(RuntimeError, "dispatch interrupted"):
                await bot.HISTORY_RUNTIME.process_history_poll_message(
                    client,
                    cast(
                        discord_diagnostics_history.DiscordHistoryMessage,
                        cast(object, message),
                    ),
                    333,
                )

        self.assertNotIn(777, client._processed_message_ids)
        self.assertTrue(
            bot.PROCESSED_MESSAGE_RUNTIME.claim_gateway_discord_message(client, message)
        )

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
            replay_eligible: bool = False,
        ) -> None:
            _ = message
            self.assertTrue(replay_eligible)
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
            await client.poll_history_channel("allowed", 333)
            await client.poll_history_channel("allowed", 333)
            channel.history_messages = [new_message, old_message]
            await client.poll_history_channel("allowed", 333)
            await discord_message_gate.process_gateway_message(
                new_message,
                deps=discord_message_gate.GatewayMessageDeps(
                    discord_client=client,
                    claim_message=lambda message: bot.PROCESSED_MESSAGE_RUNTIME.claim_discord_message(
                        client,
                        message,
                    ),
                    get_message_id=bot.PROCESSED_MESSAGE_RUNTIME.get_discord_message_id,
                    process_message=client.process_discord_message,
                    release_message=lambda message: bot.PROCESSED_MESSAGE_RUNTIME.release_gateway_discord_message(
                        client,
                        message,
                    ),
                    mark_processed=lambda message: bot.PROCESSED_MESSAGE_RUNTIME.mark_discord_message_processed(
                        client,
                        message,
                    ),
                    log=discord_logging.log_line,
                ),
            )

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
            replay_eligible: bool = False,
        ) -> None:
            _ = message, replay_eligible
            handled.append((prompt, target_thread_id))

        def mirror_thread_id(channel_id: int) -> str:
            _ = channel_id
            return "thread-1"

        def busy_state(target_thread_id: str | None) -> tuple[str, None, str]:
            _ = target_thread_id
            return "idle", None, ""

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)

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
        self.assertIn("claimed_messages=0", log_text)
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
            replay_eligible: bool = False,
        ) -> None:
            _ = (message, target_thread_id, replay_eligible)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)
            client._processed_message_ids.clear()
            bot.discord_store.cleanup_processed_discord_messages(
                bot.MIRROR_DB_PATH,
                retention_seconds=0,
                now=dt.datetime.now(dt.timezone.utc).timestamp() + 1,
            )
            await client.poll_history_channel("allowed", 333)

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
            replay_eligible: bool = False,
        ) -> None:
            _ = (message, target_thread_id)
            self.assertTrue(replay_eligible)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)
            new_message = FakeMessage(
                "new after start",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
            )
            new_message.channel = channel
            channel.history_messages = [new_message]
            await client.poll_history_channel("allowed", 333)

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
            replay_eligible: bool = False,
        ) -> None:
            _ = (message, target_thread_id, replay_eligible)
            handled.append(prompt)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)
            delayed_old_message = FakeMessage(
                "delayed old backlog",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
            )
            delayed_old_message.channel = channel
            channel.history_messages = [delayed_old_message]
            await client.poll_history_channel("allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(bot, "handle_plain_ask", fake_handle_plain_ask),
        ):
            log_text = await self._run_with_log(run_poll)

        self.assertEqual(handled, [])
        self.assertNotIn("history_poll_message channel=333", log_text)

    async def test_history_intake_failure_retries_and_commits_one_inbox_row(self) -> None:
        attempts: list[int] = []
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)
        runner_runtime = cast(
            discord_runner_runtime.RunnerRuntime,
            getattr(bot, "RUNNER_RUNTIME"),
        )

        async def flaky_intake(
            _runtime: object,
            intake_channel: object,
            prompt: str,
            target_thread_id: str,
            *,
            source_message: object,
        ) -> DeferredIntakeResult:
            _ = intake_channel
            attempts.append(int(getattr(source_message, "id")))
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is busy")
            claim = bot.discord_store.claim_deferred_discord_message(
                bot.MIRROR_DB_PATH,
                message_id=int(getattr(source_message, "id")),
                target_thread_id=target_thread_id,
                channel_id=int(getattr(getattr(source_message, "channel"), "id")),
                owner_user_id=int(getattr(getattr(source_message, "author"), "id")),
                prompt=prompt,
                source="history_poll",
                normalization_version=1,
            )
            return DeferredIntakeResult(claim.record, (), True)

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)
            offline_message = FakeMessage(
                "while app server offline",
                message_id=101,
                created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
            )
            offline_message.channel = channel
            channel.history_messages = [offline_message]
            with self.assertRaises(message_dispatch_runtime.ReplayableMessageIntakeError):
                await client.poll_history_channel("allowed", 333)
            self.assertEqual(
                bot.discord_store.list_deferred_discord_messages(bot.MIRROR_DB_PATH),
                [],
            )
            await client.poll_history_channel("allowed", 333)
            await client.poll_history_channel("allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", return_value="thread-1"),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(
                type(runner_runtime.deps.durable_queue),
                "intake_deferred",
                new=flaky_intake,
            ),
            mock.patch.object(
                type(runner_runtime),
                "_ensure_deferred_replay_task",
                return_value=None,
            ),
        ):
            await self._run_with_log(run_poll)

        inbox = bot.discord_store.list_deferred_discord_messages(bot.MIRROR_DB_PATH)
        self.assertEqual(attempts, [101, 101])
        self.assertEqual([row.message_id for row in inbox], [101])

    async def test_pre_intake_store_error_retries_on_next_history_poll(self) -> None:
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)
        runner_runtime = cast(
            discord_runner_runtime.RunnerRuntime,
            getattr(bot, "RUNNER_RUNTIME"),
        )
        lookup_attempts: list[int] = []

        def flaky_mirror_lookup(channel_id: int) -> str:
            lookup_attempts.append(channel_id)
            if len(lookup_attempts) == 1:
                raise StoreSchemaVersionError(
                    "running process cannot read the current schema"
                )
            return "thread-1"

        async def run_poll(log_path: Path) -> None:
            _ = log_path
            await client.poll_history_channel("allowed", 333)
            message = FakeMessage(
                "retry before inbox ownership",
                message_id=102,
                created_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1),
            )
            message.channel = channel
            channel.history_messages = [message]
            with self.assertRaises(message_dispatch_runtime.ReplayableMessageIntakeError):
                await client.poll_history_channel("allowed", 333)
            self.assertNotIn(102, client._processed_message_ids)
            self.assertFalse(
                bot.discord_store.is_processed_discord_message_id(
                    bot.MIRROR_DB_PATH,
                    102,
                )
            )
            self.assertEqual(
                bot.discord_store.list_deferred_discord_messages(bot.MIRROR_DB_PATH),
                [],
            )
            await client.poll_history_channel("allowed", 333)
            await client.poll_history_channel("allowed", 333)

        with (
            mock.patch.object(bot, "get_mirrored_codex_thread_id", side_effect=flaky_mirror_lookup),
            mock.patch.object(bot, "get_busy_state_for_thread", return_value=("idle", None, "")),
            mock.patch.object(bot, "is_thread_runner_busy", mock.AsyncMock(return_value=False)),
            mock.patch.object(
                type(runner_runtime),
                "_ensure_deferred_replay_task",
                return_value=None,
            ),
        ):
            await self._run_with_log(run_poll)

        inbox = bot.discord_store.list_deferred_discord_messages(bot.MIRROR_DB_PATH)
        self.assertEqual(lookup_attempts, [333, 333])
        self.assertEqual([row.message_id for row in inbox], [102])

    async def test_history_loop_continues_after_marked_no_replay_value_error(self) -> None:
        boundary = dt.datetime.now(dt.timezone.utc)
        channel = FakeHistoryChannel()
        client = FakePollClient(channel)
        client._history_poll_primed_channels.add(333)
        setattr(client, "_history_poll_channel_watermarks", {333: (boundary, 0)})
        command = FakeMessage(
            "!boom",
            message_id=201,
            created_at=boundary + dt.timedelta(seconds=1),
        )
        stable = FakeMessage(
            "recover after command failure",
            message_id=202,
            created_at=boundary + dt.timedelta(seconds=2),
        )
        command.channel = channel
        stable.channel = channel
        channel.history_messages = [command]
        handled: list[str] = []
        logs: list[str] = []
        closed = False
        sleep_count = 0

        async def fail_prefix_command(
            owner: object,
            message: object,
            command: str,
        ) -> None:
            _ = owner, message, command
            raise ValueError("unexpected command failure")

        async def handle_plain_ask(
            message: FakeMessage,
            prompt: str,
            *,
            target_thread_id: str | None = None,
            replay_eligible: bool = False,
        ) -> None:
            _ = message, target_thread_id
            self.assertTrue(replay_eligible)
            handled.append(prompt)

        async def advance_cycle(_seconds: float) -> None:
            nonlocal closed, sleep_count
            sleep_count += 1
            if sleep_count == 1:
                channel.history_messages = [stable, command]
            else:
                closed = True

        def get_targets(
            allowed_channel_ids: set[int],
            startup_channel_id: int | None,
            *,
            limit: int = 50,
        ) -> list[tuple[str, int]]:
            _ = allowed_channel_ids, startup_channel_id
            return [("allowed", 333)][:limit]

        deps = discord_history_poll.HistoryPollLoopDeps(
            allowed_channel_ids={333},
            startup_channel_id=None,
            poll_seconds=0.0,
            target_limit=5,
            is_closed=lambda: closed,
            set_last_at=lambda _value: None,
            now_iso=lambda: "2026-08-25T00:00:00+00:00",
            get_targets=get_targets,
            poll_history_channel=client.poll_history_channel,
            delivery_exceptions=bot.HISTORY_RUNTIME.deps.delivery_exceptions,
            format_traceback=lambda: "marked ValueError traceback",
            sleep=advance_cycle,
            log=logs.append,
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_DISCORD_LOG_PATH": str(Path(temp_dir) / "discord-smoke.log")},
                ),
                mock.patch.object(
                    bot,
                    "get_mirrored_codex_thread_id",
                    return_value="thread-1",
                ),
                mock.patch.object(
                    bot,
                    "get_busy_state_for_thread",
                    return_value=("idle", None, ""),
                ),
                mock.patch.object(
                    bot,
                    "is_thread_runner_busy",
                    mock.AsyncMock(return_value=False),
                ),
                mock.patch.object(bot, "handle_prefix_command", fail_prefix_command),
                mock.patch.object(bot, "handle_plain_ask", handle_plain_ask),
            ):
                await discord_history_poll.history_poll_loop(deps)

        self.assertTrue(
            bot.discord_store.is_processed_discord_message_id(bot.MIRROR_DB_PATH, 201)
        )
        self.assertEqual(handled, ["recover after command failure"])
        self.assertEqual(sleep_count, 2)
        self.assertTrue(any("history_poll_no_replay_message_failed" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
