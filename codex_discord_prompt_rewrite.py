from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import codex_discord_prompt_mapped_delivery as mapped_delivery
from codex_pro_runtime_preflight import (
    ProRuntimePreflightError,
    ProRuntimeStatus,
    run_pro_runtime_preflight,
)
from codex_pro_runtime_diagnostics import (
    ProDiagnosticCode,
    ProDiagnosticStage,
    diagnostic,
)
from codex_remote_mcp_binding import register_remote_mcp_project
from codex_remote_mcp_bridge_config import (
    ProjectTicket,
    RemoteMcpConfigurationError,
)
from codex_remote_mcp_bridge_connection import RemoteMcpBridgeError
from simdorei_mcp_common.connector_contract import (
    PRODUCTION_CONNECTOR_NAME,
    PRODUCTION_CONNECTOR_RESOURCE,
)

LogFunc = Callable[[str], None]
ProjectRegistrar = Callable[[str, str, Path, LogFunc], ProjectTicket | None]
RuntimePreflight = Callable[[], ProRuntimeStatus]
PRO_SKILL_CALL: Final = "$ask-chatgpt-pro [@Browser](plugin://browser@openai-bundled)"
PRO_CONVERSATION_SCOPE_LENGTH: Final = 24
PRO_REVIEW_MARKER: Final = "<pro-review>"
PROJECT_SCOPE_RANDOM_BYTES: Final = 24


def _rewrite_pro_command(prompt: str) -> str | None:
    parts = prompt.split(maxsplit=1)
    if not parts or parts[0].casefold() != "!pro":
        return None

    request = parts[1] if len(parts) == 2 else ""
    review_parts = request.split(maxsplit=1)
    if review_parts and review_parts[0].casefold() == "review":
        review_request = review_parts[1] if len(review_parts) == 2 else ""
        suffix = f"\n{review_request}" if review_request else ""
        return f"{PRO_SKILL_CALL} {PRO_REVIEW_MARKER}{suffix}"

    return f"{PRO_SKILL_CALL} {request}".rstrip()


def is_pro_command(prompt: str) -> bool:
    return _rewrite_pro_command(prompt) is not None


def pro_conversation_scope(thread_id: str) -> str:
    """Return a stable opaque browser-chat scope for one Codex thread."""
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return f"codex-pro-{digest[:PRO_CONVERSATION_SCOPE_LENGTH]}"


def fresh_project_scope() -> str:
    """Return a short-lived, unguessable selector for one registration."""
    return f"codex-project-{secrets.token_urlsafe(PROJECT_SCOPE_RANDOM_BYTES)}"


def rewrite_prompt(
    prompt: str,
    target_thread_id: str | None = None,
    *,
    cwd: Path,
    log: LogFunc,
    project_registrar: ProjectRegistrar = register_remote_mcp_project,
    runtime_preflight: RuntimePreflight = run_pro_runtime_preflight,
) -> mapped_delivery.PromptPreprocessResult:
    rewritten = _rewrite_pro_command(prompt)
    if rewritten is None:
        return mapped_delivery.keep_prompt(prompt)
    try:
        _ = runtime_preflight()
    except ProRuntimePreflightError as exc:
        return mapped_delivery.block_prompt(exc.diagnostic)
    if not target_thread_id:
        return mapped_delivery.keep_prompt(rewritten)
    project_scope = fresh_project_scope()
    try:
        ticket = project_registrar(target_thread_id, project_scope, cwd, log)
    except RemoteMcpConfigurationError as exc:
        return mapped_delivery.block_prompt(
            diagnostic(
                stage=ProDiagnosticStage.REMOTE_MCP,
                code=ProDiagnosticCode.REMOTE_MCP_CONFIGURATION_INVALID,
                public_message="The local project connection is configured incorrectly.",
                recovery_action="Repair the remote MCP configuration, restart the remote bot, then retry !pro.",
                internal_detail=str(exc),
            )
        )
    except RemoteMcpBridgeError as exc:
        return mapped_delivery.block_prompt(
            diagnostic(
                stage=ProDiagnosticStage.REMOTE_MCP,
                code=ProDiagnosticCode.REMOTE_MCP_CONNECTION_FAILED,
                public_message="The local project connection did not become ready.",
                recovery_action="Restart the remote bot, verify remote MCP connectivity, then retry !pro.",
                internal_detail=str(exc),
            )
        )
    if ticket is None:
        return mapped_delivery.block_prompt(
            diagnostic(
                stage=ProDiagnosticStage.REMOTE_MCP,
                code=ProDiagnosticCode.REMOTE_MCP_NOT_CONFIGURED,
                public_message="The local project connection is not configured.",
                recovery_action="Configure remote MCP, restart the remote bot, then retry !pro.",
                internal_detail="remote MCP is not configured",
            )
        )
    try:
        expired = ticket.expires_at <= datetime.now(UTC)
    except TypeError:
        return mapped_delivery.block_prompt(
            diagnostic(
                stage=ProDiagnosticStage.PROJECT_TICKET,
                code=ProDiagnosticCode.PROJECT_TICKET_TIMEZONE_INVALID,
                public_message="The local project connection returned an invalid access ticket.",
                recovery_action="Restart the remote bot, then retry !pro.",
                internal_detail="remote MCP returned a project ticket without a timezone",
            )
        )
    if expired:
        return mapped_delivery.block_prompt(
            diagnostic(
                stage=ProDiagnosticStage.PROJECT_TICKET,
                code=ProDiagnosticCode.PROJECT_TICKET_EXPIRED,
                public_message="The local project access ticket expired before delivery.",
                recovery_action="Retry !pro to request a fresh project ticket.",
                internal_detail="remote MCP returned a project ticket that is already expired",
            )
        )
    project_instruction = "\n".join(
        (
            "",
            (
                f'<local-project-mcp connector="{PRODUCTION_CONNECTOR_NAME}" '
                f'resource="{PRODUCTION_CONNECTOR_RESOURCE}">'
            ),
            "Use only the connector named in this tag and select it explicitly.",
            "Do not use Simdorei Local Project or Simdorei Local Project v12 QA.",
            f"conversation_scope: {pro_conversation_scope(target_thread_id)}",
            f"project_scope: {ticket.project_scope}",
            "Before inspecting or changing local files, call select_project exactly once",
            "with the project_scope above and connector_resource set to the resource",
            "attribute in this tag. Read a file before updating it,",
            "and pass its SHA-256 when writing an existing file.",
            "</local-project-mcp>",
        )
    )
    return mapped_delivery.keep_prompt(rewritten + project_instruction)
