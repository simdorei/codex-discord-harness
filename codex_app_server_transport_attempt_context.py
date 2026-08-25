from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


AttemptWriteCallback = Callable[[str, int, int], None]
AttemptLateSuccessCallback = Callable[[str, int, int, str, str], None]


@dataclass(frozen=True, slots=True)
class TurnStartAttemptCallbacks:
    before_write: AttemptWriteCallback
    after_write: AttemptWriteCallback
    late_success: AttemptLateSuccessCallback


_TURN_START_ATTEMPT: ContextVar[TurnStartAttemptCallbacks | None] = ContextVar(
    "codex_turn_start_attempt",
    default=None,
)


@contextmanager
def bind_turn_start_attempt(
    *,
    before_write: AttemptWriteCallback,
    after_write: AttemptWriteCallback,
    late_success: AttemptLateSuccessCallback,
) -> Generator[None, None, None]:
    token: Token[TurnStartAttemptCallbacks | None] = _TURN_START_ATTEMPT.set(
        TurnStartAttemptCallbacks(before_write, after_write, late_success)
    )
    try:
        yield
    finally:
        _TURN_START_ATTEMPT.reset(token)


def get_turn_start_attempt_callbacks() -> TurnStartAttemptCallbacks | None:
    return _TURN_START_ATTEMPT.get()
