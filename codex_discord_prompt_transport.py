from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from codex_pro_prompt_contract import is_pro_skill_prompt


class PromptRelay(Protocol):
    def finish(self) -> None: ...


class PromptDeliveryResult(Protocol):
    @property
    def exit_code(self) -> int: ...

    @property
    def output(self) -> str: ...

    @property
    def thread_id(self) -> str | None: ...

    @property
    def target_ref(self) -> str: ...

    @property
    def session_path(self) -> str | None: ...

    @property
    def start_offset(self) -> int | None: ...

    @property
    def delivery_pending(self) -> bool: ...


RelayT = TypeVar("RelayT", bound=PromptRelay)
RelayContraT = TypeVar("RelayContraT", bound=PromptRelay, contravariant=True)
DeliveryResultT = TypeVar("DeliveryResultT", bound=PromptDeliveryResult)
SteeringResultT = TypeVar("SteeringResultT")
PromptNoWait = Callable[[str, str | None], tuple[int, str]]
TransportEnabled = Callable[[], bool]
StartTurnNoWait = Callable[[str, str | None], DeliveryResultT]
MakeSteeringResult = Callable[[DeliveryResultT], SteeringResultT]
WatchStream = Callable[[SteeringResultT, RelayT], tuple[int, str]]
LogFunc = Callable[[str], None]


class LegacyAskStream(Protocol[RelayContraT]):
    def __call__(
        self,
        prompt: str,
        relay: RelayContraT,
        *,
        force_while_busy: bool = False,
        wait: bool = True,
        target_thread_id: str | None = None,
    ) -> tuple[int, str]: ...


@dataclass(frozen=True, slots=True)
class PromptTransportDeps(Generic[RelayT, DeliveryResultT, SteeringResultT]):
    app_server_transport_enabled: TransportEnabled
    run_resident_prompt_no_wait: PromptNoWait
    run_legacy_prompt_no_wait: PromptNoWait
    start_turn_no_wait: StartTurnNoWait[DeliveryResultT]
    make_steering_prompt_result: MakeSteeringResult[DeliveryResultT, SteeringResultT]
    run_watch_stream: WatchStream[SteeringResultT, RelayT]
    run_legacy_stream: LegacyAskStream[RelayT]
    log: LogFunc


def _transport_error_output(exc: Exception) -> str:
    message = str(exc)
    lines = [f"ERROR: resident app-server transport failed: {message}"]
    if "Thread not found:" in message:
        lines.extend(
            [
                "",
                "The mapped Codex thread exists in local history, but the resident app-server cannot open it.",
                "Run `!mirror sync`; it refreshes app-server thread availability and removes stale mirror mappings.",
            ]
        )
    if isinstance(exc, TimeoutError) and "thread/resume" in message:
        lines.extend(
            [
                "",
                "A large conversation history or temporary PC load can delay restoring this Codex thread.",
                "Run `!resume` to retry the restore, then resend the original message.",
                "The failed prompt was not resent automatically.",
            ]
        )
    if isinstance(exc, TimeoutError) and "thread/read" in message:
        lines.extend(
            [
                "",
                "Only this thread status check timed out. This timeout did not stop or restart other Codex threads.",
                "Run `!resume` to check this thread again, then resend the original message.",
                "The failed prompt was not sent.",
            ]
        )
    return "\n".join(lines)


def _log_transport_failure(log: LogFunc, *, event: str, target_thread_id: str | None, exc: Exception) -> None:
    log(f"{event} target={target_thread_id or '-'} " + f"error_type={type(exc).__name__} error={str(exc)[:300]}")


def run_transport_prompt_no_wait(
    prompt: str,
    target_thread_id: str | None,
    deps: PromptTransportDeps[RelayT, DeliveryResultT, SteeringResultT],
) -> tuple[int, str]:
    resident_enabled = deps.app_server_transport_enabled() or is_pro_skill_prompt(prompt)
    if not resident_enabled:
        return deps.run_legacy_prompt_no_wait(prompt, target_thread_id)
    try:
        return deps.run_resident_prompt_no_wait(prompt, target_thread_id)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - transport boundary surfaces resident failure.
        _log_transport_failure(
            deps.log,
            event="app_server_prompt_failed",
            target_thread_id=target_thread_id,
            exc=exc,
        )
        return 1, _transport_error_output(exc)


def run_ask_stream(
    prompt: str,
    relay: RelayT,
    *,
    force_while_busy: bool = False,
    wait: bool = True,
    target_thread_id: str | None = None,
    deps: PromptTransportDeps[RelayT, DeliveryResultT, SteeringResultT],
) -> tuple[int, str]:
    resident_enabled = deps.app_server_transport_enabled() or is_pro_skill_prompt(prompt)
    if not resident_enabled:
        return deps.run_legacy_stream(
            prompt,
            relay,
            force_while_busy=force_while_busy,
            wait=wait,
            target_thread_id=target_thread_id,
        )
    try:
        result = deps.start_turn_no_wait(prompt, target_thread_id)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK - transport boundary surfaces resident failure.
        _log_transport_failure(
            deps.log,
            event="app_server_stream_prompt_failed",
            target_thread_id=target_thread_id,
            exc=exc,
        )
        relay.finish()
        return 1, _transport_error_output(exc)
    if result.exit_code == 0 and wait and result.session_path and result.start_offset is not None:
        return deps.run_watch_stream(deps.make_steering_prompt_result(result), relay)
    relay.finish()
    return result.exit_code, result.output
