from __future__ import annotations

import json
import os
import runpy
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest import mock


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


def test_generated_lease_token_cannot_be_parsed_as_an_option(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    with (
        mock.patch.dict(os.environ, environment, clear=True),
        mock.patch("secrets.token_urlsafe", return_value="-leading-hyphen-token-value"),
    ):
        script_globals = runpy.run_path(str(SCRIPT))
        acquire = cast(
            Callable[[str], dict[str, str]],
            cast(object, script_globals["acquire"]),
        )
        lease = acquire(SCOPE)

    lease_token = str(lease["lease_token"])
    completed = _run_process(
        environment,
        "release",
        "--scope",
        SCOPE,
        "--lease-token",
        lease_token,
    )

    assert not lease_token.startswith("-")
    assert completed.returncode == 0, completed.stderr


def test_expired_but_still_owned_lease_can_save(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    lease = _run(environment, "acquire", "--scope", SCOPE)
    _expire_lease(environment)

    saved = _run(
        environment,
        "set",
        "--scope",
        SCOPE,
        "--url",
        URL,
        "--lease-token",
        lease["lease_token"],
    )

    assert saved == {"status": "saved"}
    assert _run(environment, "acquire", "--scope", SCOPE) == {
        "status": "found",
        "url": URL,
    }


def test_reacquired_lease_fences_stale_creator_but_current_creator_can_save_after_expiry(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    stale_lease = _run(environment, "acquire", "--scope", SCOPE)
    _expire_lease(environment)
    current_lease = _run(environment, "acquire", "--scope", SCOPE)
    _expire_lease(environment)

    rejected = _run_process(
        environment,
        "set",
        "--scope",
        SCOPE,
        "--url",
        URL,
        "--lease-token",
        stale_lease["lease_token"],
    )
    saved = _run(
        environment,
        "set",
        "--scope",
        SCOPE,
        "--url",
        URL,
        "--lease-token",
        current_lease["lease_token"],
    )

    assert rejected.returncode == 2
    assert "missing or was replaced" in rejected.stderr
    assert saved == {"status": "saved"}


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "SIMDOREI_PRO_CONVERSATION_DB": str(tmp_path / "conversations.sqlite3"),
    }


def _expire_lease(environment: dict[str, str]) -> None:
    with sqlite3.connect(environment["SIMDOREI_PRO_CONVERSATION_DB"]) as connection:
        _ = connection.execute(
            "UPDATE conversations SET lease_expires_at = 0 WHERE scope = ?",
            (SCOPE,),
        )


def _run(
    environment: dict[str, str],
    *arguments: str,
) -> dict[str, str]:
    completed = _run_process(environment, *arguments)
    assert completed.returncode == 0, completed.stderr
    value = cast(object, json.loads(completed.stdout))
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    return {str(key): str(item) for key, item in mapping.items()}


def _run_process(
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )
