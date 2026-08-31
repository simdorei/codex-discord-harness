from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import unittest

from codex_discord_durable_queue_restore import QueueRestoreUnstableError
from codex_discord_durable_queue_restore import QueueRestoreBot
from codex_discord_durable_queue_runtime import DeferredInboxRestore
from codex_discord_runner_queue import QueueJobValue
from codex_discord_runner_runtime import RunnerRuntime, RunnerRuntimeDeps


class _Bot:
    pass


class _DurableQueue:
    def __init__(self) -> None:
        self.restore_calls = 0
        self.restored = asyncio.Event()

    async def restore_deferred_inbox(self, _bot: object) -> DeferredInboxRestore:
        return DeferredInboxRestore((), ())

    async def restore_jobs(self, _bot: object) -> list[dict[str, QueueJobValue]]:
        self.restore_calls += 1
        if self.restore_calls == 1:
            raise QueueRestoreUnstableError("app server unavailable")
        self.restored.set()
        return []


class RunnerRuntimeRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_unhealthy_startup_retries_restore_after_ready_returns(self) -> None:
        logs: list[str] = []
        durable = _DurableQueue()
        deps = cast(
            RunnerRuntimeDeps,
            cast(
                object,
                SimpleNamespace(
                    durable_queue=durable,
                    log=logs.append,
                ),
            ),
        )
        runtime = RunnerRuntime(deps)

        bot = cast(QueueRestoreBot, cast(object, _Bot()))
        restored_count = await runtime.restore_durable_queue_runners(bot)
        await asyncio.wait_for(durable.restored.wait(), timeout=2.0)

        self.assertEqual(restored_count, 0)
        self.assertEqual(durable.restore_calls, 2)
        self.assertTrue(any("queue_restore_deferred" in line for line in logs))
        self.assertTrue(any("queue_restore_retry_done" in line for line in logs))


if __name__ == "__main__":
    _ = unittest.main()
