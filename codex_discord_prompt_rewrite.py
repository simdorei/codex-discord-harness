from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

import codex_discord_prompt_mapped_delivery as mapped_delivery


LogFunc = Callable[[str], None]
PRO_SKILL_CALL: Final = (
    "$ask-chatgpt-pro "
    "[@Browser](plugin://browser@openai-bundled)"
)
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


def rewrite_prompt(prompt: str, *, cwd: Path, log: LogFunc) -> mapped_delivery.PromptPreprocessResult:
    _ = cwd, log
    rewritten = _rewrite_pro_command(prompt)
    return mapped_delivery.keep_prompt(rewritten if rewritten is not None else prompt)
