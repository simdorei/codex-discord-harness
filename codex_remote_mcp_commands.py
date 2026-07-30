from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from codex_remote_mcp_command_sandbox import (
    CommandSandboxError,
    sandbox_arguments,
)
from codex_remote_mcp_files import ProjectFileError
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.operation_outputs import (
    CommandDescriptor,
    CommandListOutput,
    CommandRunOutput,
)
from simdorei_mcp_common.operation_requests import CommandRunRequest

RiskTier = Literal["read", "verify", "network", "destructive"]
MAX_OUTPUT_BYTES: Final = 12_000
SAFE_SCRIPT_NAME: Final = re.compile(r"^[A-Za-z0-9:_-]+$")
SAFE_SCRIPT_TOKEN: Final = re.compile(r"^[A-Za-z0-9_./:@=+-]+$")
VERIFY_NAME: Final = re.compile(
    r"^(test|tests|typecheck|type-check|lint|check|verify|build|compile)$",
    re.IGNORECASE,
)
NETWORK_TEXT: Final = re.compile(
    r"\b(curl|wget)\b|\b(npm|pnpm|yarn|bun)\s+(install|add|update)"
    r"|\bgit\s+(pull|fetch|clone|push)\b",
    re.IGNORECASE,
)
DESTRUCTIVE_TEXT: Final = re.compile(
    r"\bsudo\b|\brm\s+-\w*[rf]\w*\b|\bgit\s+(clean|reset)\b"
    r"|\b(deploy|publish|release|destroy)\b",
    re.IGNORECASE,
)
ENVIRONMENT_ALLOWLIST: Final = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "COMSPEC",
    "ComSpec",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "CODEX_HOME",
)


class ProjectCommandError(ProjectFileError):
    """Raised when a discovered project command cannot run safely."""


@dataclass(frozen=True, slots=True)
class DiscoveredCommand:
    descriptor: CommandDescriptor
    arguments: tuple[str, ...]


def list_commands(root: Path) -> CommandListOutput:
    """Discover allowlist-eligible project commands from local manifests."""
    commands = _discover_commands(root)
    return CommandListOutput(
        commands=tuple(command.descriptor for command in commands),
    )


def run_command(root: Path, request: CommandRunRequest) -> CommandRunOutput:
    """Run one discovered non-network command without arbitrary shell input."""
    commands = {
        command.descriptor.command_id: command
        for command in _discover_commands(root)
    }
    selected = commands.get(request.command_id)
    if selected is None:
        raise ProjectCommandError(
            request.command_id,
            "command is not in a discovered project manifest",
        )
    if selected.descriptor.risk_tier in {"network", "destructive"}:
        raise ProjectCommandError(
            request.command_id,
            f"{selected.descriptor.risk_tier} command is not remotely executable",
        )
    try:
        sandboxed = sandbox_arguments(root, selected.arguments)
    except CommandSandboxError as exc:
        raise ProjectCommandError(request.command_id, str(exc)) from exc
    started = time.monotonic()
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            completed = subprocess.run(
                sandboxed,
                cwd=root,
                env=_safe_environment(),
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=request.timeout_seconds,
            )
            stdout, stdout_cut = _read_bounded(stdout_file)
            stderr, stderr_cut = _read_bounded(stderr_file)
    except FileNotFoundError as exc:
        raise ProjectCommandError(
            request.command_id,
            f"command executable was not found: {sandboxed[0]}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProjectCommandError(
            request.command_id,
            f"command timed out after {request.timeout_seconds} seconds",
        ) from exc
    return CommandRunOutput(
        command_id=request.command_id,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.monotonic() - started) * 1_000),
        truncated=stdout_cut or stderr_cut,
    )


def _discover_commands(root: Path) -> tuple[DiscoveredCommand, ...]:
    commands: list[DiscoveredCommand] = []
    package_path = root / "package.json"
    if package_path.is_file():
        commands.extend(_package_commands(package_path))
    commands.extend(_standard_commands(root))
    return tuple(commands)


def _package_commands(package_path: Path) -> tuple[DiscoveredCommand, ...]:
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectCommandError(
            "package.json",
            "package manifest could not be read",
        ) from exc
    scripts = payload.get("scripts") if isinstance(payload, dict) else None
    if not isinstance(scripts, dict):
        return ()
    commands: list[DiscoveredCommand] = []
    for name, script in scripts.items():
        if not isinstance(name, str) or not isinstance(script, str):
            continue
        if SAFE_SCRIPT_NAME.fullmatch(name) is None:
            continue
        safe_arguments = _safe_package_arguments(script)
        commands.append(
            DiscoveredCommand(
                descriptor=CommandDescriptor(
                    command_id=f"npm:{name}",
                    display=script,
                    source="package.json",
                    risk_tier=(
                        _risk_tier(name, script)
                        if safe_arguments is not None
                        else "destructive"
                    ),
                ),
                arguments=safe_arguments or (),
            )
        )
    return tuple(commands)


def _standard_commands(root: Path) -> tuple[DiscoveredCommand, ...]:
    commands: list[DiscoveredCommand] = []
    if (root / "Cargo.toml").is_file():
        commands.extend(
            (
                _command("cargo:test", "cargo test", "Cargo.toml", ("cargo", "test")),
                _command("cargo:clippy", "cargo clippy", "Cargo.toml", ("cargo", "clippy")),
            )
        )
    if (root / "go.mod").is_file():
        commands.append(
            _command("go:test", "go test ./...", "go.mod", ("go", "test", "./..."))
        )
    if (root / "pubspec.yaml").is_file():
        commands.append(
            _command("flutter:test", "flutter test", "pubspec.yaml", ("flutter", "test"))
        )
    if (root / "pyproject.toml").is_file() and (root / "tests").is_dir():
        runner = ("uv", "run", "pytest") if (root / "uv.lock").is_file() else (
            sys.executable,
            "-m",
            "pytest",
        )
        commands.append(_command("python:test", "pytest", "pyproject.toml", runner))
    return tuple(commands)


def _command(
    command_id: str,
    display: str,
    source: str,
    arguments: tuple[str, ...],
) -> DiscoveredCommand:
    return DiscoveredCommand(
        descriptor=CommandDescriptor(
            command_id=command_id,
            display=display,
            source=source,
            risk_tier="verify",
        ),
        arguments=arguments,
    )


def _risk_tier(name: str, script: str) -> RiskTier:
    if DESTRUCTIVE_TEXT.search(script) is not None:
        return "destructive"
    if NETWORK_TEXT.search(script) is not None:
        return "network"
    if VERIFY_NAME.fullmatch(name) is not None:
        return "verify"
    return "read"


def _safe_package_arguments(script: str) -> tuple[str, ...] | None:
    tokens = tuple(script.split())
    if (
        len(tokens) < 2
        or any(SAFE_SCRIPT_TOKEN.fullmatch(token) is None for token in tokens)
        or tokens[0].casefold() != "node"
        or tokens[1] != "--test"
    ):
        return None
    node = shutil.which("node")
    return (
        node or "node",
        "--preserve-symlinks-main",
        *tokens[1:],
    )


def _safe_environment() -> dict[str, str]:
    return {
        key: value
        for key in ENVIRONMENT_ALLOWLIST
        if (value := os.environ.get(key)) is not None
    }


def _read_bounded(handle) -> tuple[str, bool]:
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(0)
    if size <= MAX_OUTPUT_BYTES:
        return redact(handle.read().decode("utf-8", errors="replace")), False
    head = handle.read(8_000).decode("utf-8", errors="replace")
    handle.seek(-4_000, os.SEEK_END)
    tail = handle.read(4_000).decode("utf-8", errors="replace")
    return redact(f"{head}\n...[output truncated]...\n{tail}"), True
