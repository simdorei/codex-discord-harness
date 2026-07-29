from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Final

import codex_discord_prompt_mapped_delivery as mapped_delivery
from codex_remote_mcp_binding import issue_remote_mcp_binding
from codex_remote_mcp_bridge_config import BindingTicket

LogFunc = Callable[[str], None]
BindingIssuer = Callable[[str, Path, LogFunc], BindingTicket | None]
PRO_SKILL_CALL: Final = (
    "$ask-chatgpt-pro "
    "[@Browser](plugin://browser@openai-bundled)"
)
PRO_CONVERSATION_SCOPE_LENGTH: Final = 24
PRO_REVIEW_MARKER: Final = "<pro-review>"


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


def rewrite_prompt(
    prompt: str,
    target_thread_id: str | None = None,
    *,
    cwd: Path,
    log: LogFunc,
    binding_issuer: BindingIssuer = issue_remote_mcp_binding,
) -> mapped_delivery.PromptPreprocessResult:
    rewritten = _rewrite_pro_command(prompt)
    if rewritten is None:
        return mapped_delivery.keep_prompt(prompt)
    if not target_thread_id:
        return mapped_delivery.keep_prompt(rewritten)
    ticket = binding_issuer(target_thread_id, cwd, log)
    if ticket is None:
        return mapped_delivery.keep_prompt(rewritten)
    binding_instruction = "\n".join(
        (
            "",
            "<local-project-mcp>",
            "A local Codex project is available through the configured MCP connector.",
            f"conversation_scope: {pro_conversation_scope(target_thread_id or '')}",
            "Before inspecting or changing local files, call bind_project exactly once",
            f"with binding_code: {ticket.binding_code}",
            "Do not repeat or reveal the binding code. Read a file before updating it,",
            "and pass its SHA-256 when writing an existing file.",
            "</local-project-mcp>",
        )
    )
    return mapped_delivery.keep_prompt(rewritten + binding_instruction)
