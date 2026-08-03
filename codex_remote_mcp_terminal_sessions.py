from __future__ import annotations

import threading
from pathlib import Path
from typing import final

from codex_remote_mcp_terminal_engine import TerminalExecutionEngine


@final
class TerminalSessionRegistry:
    """Own one terminal engine per bound thread and ChatGPT session."""

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._engines: dict[str, TerminalExecutionEngine] = {}

    def for_session(
        self,
        thread_id: str,
        root: Path,
        session_id: str,
    ) -> TerminalExecutionEngine:
        with self._lock:
            current = self._engines.get(thread_id)
            if (
                current is not None
                and current.session_id == session_id
                and current.root == root.resolve(strict=True)
            ):
                return current
            if current is not None:
                del self._engines[thread_id]
                current.close()
            created = TerminalExecutionEngine(root, session_id=session_id)
            self._engines[thread_id] = created
            return created

    def close_thread(self, thread_id: str) -> None:
        with self._lock:
            current = self._engines.pop(thread_id, None)
            if current is not None:
                current.close()

    def close_all(self) -> None:
        with self._lock:
            engines = tuple(self._engines.values())
            self._engines.clear()
            failures: list[Exception] = []
            for engine in engines:
                try:
                    engine.close()
                except Exception as exc:  # noqa: BLE001 - finish every owned cleanup.
                    failures.append(exc)
            if failures:
                raise failures[0]


__all__ = ["TerminalSessionRegistry"]
