from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import final

from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    BridgeResult,
    GatewayCommand,
    OperationErrorResult,
)


@dataclass(frozen=True, slots=True)
class WorkerCompletion:
    generation: int
    result: BridgeResult


@final
class BridgeCommandWorkers:
    """Runs a bounded number of local commands away from the socket loop."""

    def __init__(
        self,
        dispatcher: LocalProjectDispatcher,
        *,
        log: Callable[[str], None],
        max_workers: int = 4,
        max_pending: int = 16,
    ) -> None:
        self._dispatcher = dispatcher
        self._log = log
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="codex-remote-mcp-worker",
        )
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._completed: queue.Queue[WorkerCompletion] = queue.Queue()
        self._generation_lock = threading.Lock()
        self._generation = 0
        self._close_lock = threading.Lock()
        self._closed = False

    def begin_connection(self) -> int:
        with self._generation_lock:
            self._generation += 1
            return self._generation

    def submit(
        self,
        generation: int,
        command: GatewayCommand,
    ) -> OperationErrorResult | None:
        if not self._capacity.acquire(blocking=False):
            self._log("remote_mcp_bridge_command_rejected reason=capacity")
            return OperationErrorResult(
                request_id=command.request_id,
                error_code="bridge_busy",
                message="The local bridge is busy. Retry this request shortly.",
            )
        future = self._executor.submit(self._dispatcher.execute, command)
        _ = future.add_done_callback(
            lambda completed: self._finish(generation, command, completed)
        )
        return None

    def drain(self, generation: int) -> tuple[BridgeResult, ...]:
        current: list[BridgeResult] = []
        while True:
            try:
                completed = self._completed.get_nowait()
            except queue.Empty:
                return tuple(current)
            if completed.generation == generation:
                current.append(completed.result)
            else:
                self._log("remote_mcp_bridge_result_discarded reason=stale_connection")

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _finish(
        self,
        generation: int,
        command: GatewayCommand,
        future: Future[BridgeResult],
    ) -> None:
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            self._log(
                "remote_mcp_bridge_command_failed " + f"error={type(exc).__name__}"
            )
            result = OperationErrorResult(
                request_id=command.request_id,
                error_code="internal_error",
                message="The local bridge command failed unexpectedly.",
            )
        finally:
            self._capacity.release()
        self._completed.put(WorkerCompletion(generation, result))


__all__ = ["BridgeCommandWorkers"]
