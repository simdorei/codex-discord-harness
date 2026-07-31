from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(
    "plugins/codex-discord-remote/skills/ask-chatgpt-pro/scripts/conversation_map.py"
)
PROJECT_SCRIPT = Path(".agents/skills/ask-chatgpt-pro/scripts/conversation_map.py")
SCOPE = "codex-pro-0123456789abcdef01234567"
URL = "https://chatgpt.com/c/01234567-89ab-cdef-0123-456789abcdef"


def test_conversation_creation_lease_and_url_survive_new_processes(
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "SIMDOREI_PRO_CONVERSATION_DB": str(tmp_path / "conversations.sqlite3"),
    }

    first = _run(environment, "acquire", "--scope", SCOPE)
    busy = _run(environment, "acquire", "--scope", SCOPE)
    saved = _run(
        environment,
        "set",
        "--scope",
        SCOPE,
        "--url",
        URL,
        "--lease-token",
        first["lease_token"],
    )
    restored = _run(environment, "acquire", "--scope", SCOPE)

    assert first["status"] == "acquired"
    assert busy == {"status": "busy"}
    assert saved == {"status": "saved"}
    assert restored == {"status": "found", "url": URL}


def test_project_and_plugin_skill_ship_the_same_conversation_store() -> None:
    assert SCRIPT.read_bytes() == PROJECT_SCRIPT.read_bytes()


def _run(
    environment: dict[str, str],
    *arguments: str,
) -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return {str(key): str(item) for key, item in value.items()}
