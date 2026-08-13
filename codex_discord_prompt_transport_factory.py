from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Final, TypeAlias, TypeVar

import codex_app_server_transport as app_server_transport
import codex_app_server_transport_delivery as app_server_delivery
import codex_discord_app_server as discord_app_server
import codex_discord_app_server_admission as discord_app_server_admission
import codex_discord_prompt_transport as prompt_transport
import codex_discord_runtime_config as runtime_config
import codex_discord_stream as discord_stream
import codex_discord_ui_ask as discord_ui_ask
import codex_pro_browser_evidence as pro_browser_evidence


RelayT = TypeVar("RelayT", bound=discord_stream.AskStreamRelay)
SteeringResultT = TypeVar("SteeringResultT")
AppServerDeliveryResult: TypeAlias = app_server_transport.AppServerDeliveryResult
AppServerStartTurnNoWait: TypeAlias = prompt_transport.StartTurnNoWait[AppServerDeliveryResult]
DEFAULT_APP_SERVER_DELIVERY_CONFIRM_TIMEOUT_SECONDS: Final = 25.0


class ProMappedThreadRequiredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Pro in-app Browser requires a mapped Codex thread.")


class ProDeliveryIdentityMissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Pro Desktop IPC delivery returned no exact thread and turn identity."
        )


def get_app_server_delivery_confirm_timeout() -> float:
    return runtime_config.get_steering_delivery_confirm_timeout(
        default=DEFAULT_APP_SERVER_DELIVERY_CONFIRM_TIMEOUT_SECONDS,
    )


def make_prompt_transport_deps(
    *,
    bridge_module: app_server_delivery.BridgeModule,
    app_server_transport_enabled: prompt_transport.TransportEnabled,
    run_legacy_prompt_no_wait: prompt_transport.PromptNoWait,
    make_steering_prompt_result: prompt_transport.MakeSteeringResult[
        AppServerDeliveryResult,
        SteeringResultT,
    ],
    run_watch_stream: prompt_transport.WatchStream[SteeringResultT, RelayT],
    run_bridge_command_stream: discord_stream.RunBridgeCommandStreamFunc,
    ui_fallback_lock: AbstractContextManager[bool],
    log: prompt_transport.LogFunc,
    run_resident_prompt_no_wait: prompt_transport.PromptNoWait | None = None,
    start_turn_no_wait: AppServerStartTurnNoWait | None = None,
    complete_pro_browser_session: prompt_transport.CompleteProBrowserSession | None = None,
) -> prompt_transport.PromptTransportDeps[RelayT, AppServerDeliveryResult, SteeringResultT]:
    def prepare_pro_browser_session(target_thread_id: str | None) -> None:
        if not target_thread_id:
            raise ProMappedThreadRequiredError
        log(f"pro_browser_session_admitted target={target_thread_id}")

    def complete_pro_browser_session_impl(
        target_thread_id: str | None,
        turn_id: str | None,
    ) -> None:
        if complete_pro_browser_session is not None:
            complete_pro_browser_session(target_thread_id, turn_id)
            return
        if not target_thread_id or not turn_id:
            raise ProDeliveryIdentityMissingError
        pro_browser_evidence.require_available_evidence(target_thread_id, turn_id)
        log(f"pro_browser_session_verified target={target_thread_id} turn={turn_id}")

    def run_pro_prompt_impl(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
        argv = discord_stream.build_stream_ask_argv(
            prompt,
            wait=True,
            target_thread_id=target_thread_id,
            use_sidecar=False,
            ipc_recover_ui=True,
            no_fallback=True,
        )
        return run_bridge_command_stream(argv, lambda _line: None)

    def run_resident_prompt_no_wait_impl(prompt: str, target_thread_id: str | None) -> tuple[int, str]:
        if run_resident_prompt_no_wait is not None:
            return run_resident_prompt_no_wait(prompt, target_thread_id)
        return discord_app_server.run_prompt_no_wait(
            prompt,
            target_thread_id,
            transport_module=app_server_transport,
            bridge_module=bridge_module,
            client=app_server_transport.DEFAULT_CLIENT,
            confirm_timeout_sec=get_app_server_delivery_confirm_timeout(),
        )

    def start_turn_no_wait_impl(prompt: str, target_thread_id: str | None) -> AppServerDeliveryResult:
        if start_turn_no_wait is not None:
            return start_turn_no_wait(prompt, target_thread_id)
        expected_generation = discord_app_server_admission.current_expected_app_server_generation()
        if expected_generation is None:
            return app_server_transport.steer_or_start_no_wait(
                app_server_transport.DEFAULT_CLIENT,
                prompt,
                target_thread_id,
                bridge_module=bridge_module,
                confirm_timeout_sec=get_app_server_delivery_confirm_timeout(),
            )
        return app_server_transport.steer_or_start_no_wait(
            app_server_transport.DEFAULT_CLIENT,
            prompt,
            target_thread_id,
            bridge_module=bridge_module,
            confirm_timeout_sec=get_app_server_delivery_confirm_timeout(),
            expected_generation=expected_generation,
        )

    def run_legacy_stream(
        prompt: str,
        relay: RelayT,
        *,
        force_while_busy: bool = False,
        wait: bool = True,
        target_thread_id: str | None = None,
    ) -> tuple[int, str]:
        return discord_stream.run_ask_stream(
            prompt,
            relay,
            force_while_busy=force_while_busy,
            wait=wait,
            target_thread_id=target_thread_id,
            use_sidecar=False,
            no_fallback=True,
            allow_ui_fallback=False,
            run_bridge_command_stream_func=run_bridge_command_stream,
            should_retry_ask_with_ui_func=discord_ui_ask.should_retry_ask_with_ui,
            build_ui_ask_argv_func=discord_ui_ask.build_ui_ask_argv,
            ui_fallback_lock=ui_fallback_lock,
        )

    return prompt_transport.PromptTransportDeps(
        app_server_transport_enabled=app_server_transport_enabled,
        prepare_pro_browser_session=prepare_pro_browser_session,
        complete_pro_browser_session=complete_pro_browser_session_impl,
        run_pro_prompt=run_pro_prompt_impl,
        run_resident_prompt_no_wait=run_resident_prompt_no_wait_impl,
        run_legacy_prompt_no_wait=run_legacy_prompt_no_wait,
        start_turn_no_wait=start_turn_no_wait_impl,
        make_steering_prompt_result=make_steering_prompt_result,
        run_watch_stream=run_watch_stream,
        run_legacy_stream=run_legacy_stream,
        log=log,
    )
