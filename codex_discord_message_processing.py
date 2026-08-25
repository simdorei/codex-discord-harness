from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class InboundProcessingDisposition(StrEnum):
    RETRYABLE = "retryable"
    NO_REPLAY = "no_replay"
    OWNED = "owned"


@dataclass(slots=True)
class InboundProcessingState:
    disposition: InboundProcessingDisposition

    @property
    def retryable(self) -> bool:
        return self.disposition is InboundProcessingDisposition.RETRYABLE

    def mark_no_replay(self) -> None:
        if self.disposition is not InboundProcessingDisposition.OWNED:
            self.disposition = InboundProcessingDisposition.NO_REPLAY

    def mark_owned(self) -> None:
        self.disposition = InboundProcessingDisposition.OWNED


_CURRENT_STATE: ContextVar[InboundProcessingState | None] = ContextVar(
    "discord_inbound_processing_state",
    default=None,
)
_NO_REPLAY_FAILURE_ATTRIBUTE = "_discord_no_replay_failure"


@contextmanager
def bind_inbound_processing_state(
    state: InboundProcessingState,
) -> Iterator[None]:
    token = _CURRENT_STATE.set(state)
    try:
        yield
    finally:
        _CURRENT_STATE.reset(token)


def current_inbound_processing_state() -> InboundProcessingState | None:
    return _CURRENT_STATE.get()


def mark_current_message_no_replay() -> None:
    state = current_inbound_processing_state()
    if state is not None:
        state.mark_no_replay()


def mark_current_message_owned() -> None:
    state = current_inbound_processing_state()
    if state is not None:
        state.mark_owned()


def mark_failure_no_replay(exc: BaseException) -> None:
    setattr(exc, _NO_REPLAY_FAILURE_ATTRIBUTE, True)


def failure_is_no_replay(exc: BaseException) -> bool:
    return getattr(exc, _NO_REPLAY_FAILURE_ATTRIBUTE, False) is True
