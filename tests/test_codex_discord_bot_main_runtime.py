from __future__ import annotations

from builtins import BaseExceptionGroup
from collections.abc import Callable
from typing import Protocol, cast, final

import pytest

import codex_discord_bot_main_runtime
from codex_discord_bot_main_runtime import BotRunner, RuntimeCloser


class _RunBotAndCloseServices(Protocol):
    def __call__(
        self,
        bot: BotRunner,
        token: str,
        *,
        close_bridge: RuntimeCloser,
        close_app_server: RuntimeCloser,
    ) -> None: ...


_run_bot_and_close_services = cast(
    _RunBotAndCloseServices,
    cast(
        object, getattr(codex_discord_bot_main_runtime, "_run_bot_and_close_services")
    ),
)


@final
class _RecordingRunner:
    def __init__(
        self,
        events: list[str],
        error: BaseException | None = None,
    ) -> None:
        self._events = events
        self._error = error

    def run(self, token: str, *, log_handler: None) -> None:
        assert token == "token"
        assert log_handler is None
        self._events.append("run")
        if self._error is not None:
            raise self._error


def _closer(
    events: list[str],
    name: str,
    error: BaseException | None = None,
) -> Callable[[], None]:
    def close() -> None:
        events.append(name)
        if error is not None:
            raise error

    return close


def test_bot_failure_and_bridge_cleanup_failure_still_close_app_server() -> None:
    events: list[str] = []
    runner = _RecordingRunner(events, RuntimeError("bot failed"))

    with pytest.raises(BaseExceptionGroup) as captured:
        _run_bot_and_close_services(
            runner,
            "token",
            close_bridge=_closer(events, "bridge", ValueError("bridge failed")),
            close_app_server=_closer(events, "app-server"),
        )

    assert events == ["run", "bridge", "app-server"]
    assert tuple(type(error) for error in captured.value.exceptions) == (
        RuntimeError,
        ValueError,
    )


def test_every_cleanup_failure_is_reported_after_a_normal_bot_exit() -> None:
    events: list[str] = []
    runner = _RecordingRunner(events)

    with pytest.raises(BaseExceptionGroup) as captured:
        _run_bot_and_close_services(
            runner,
            "token",
            close_bridge=_closer(events, "bridge", ValueError("bridge failed")),
            close_app_server=_closer(
                events,
                "app-server",
                OSError("app server failed"),
            ),
        )

    assert events == ["run", "bridge", "app-server"]
    assert tuple(type(error) for error in captured.value.exceptions) == (
        ValueError,
        OSError,
    )
