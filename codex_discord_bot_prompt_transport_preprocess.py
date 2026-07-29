from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import cast

import codex_discord_prompt_mapped_delivery as mapped_delivery
import codex_discord_prompt_rewrite as prompt_rewrite


def make_prompt_preprocessor(module: ModuleType) -> mapped_delivery.PromptPreprocessor:
    return partial(
        prompt_rewrite.rewrite_prompt,
        cwd=cast(Path, getattr(module, "SCRIPT_DIR")),
        log=cast(Callable[[str], None], getattr(module, "log_line")),
    )


def make_discord_origin_prompt_marker(module: ModuleType) -> mapped_delivery.DiscordOriginPromptMarker:
    return cast(
        mapped_delivery.DiscordOriginPromptMarker,
        getattr(module, "mark_recent_discord_origin_prompt"),
    )
