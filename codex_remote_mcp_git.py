from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

from codex_remote_mcp_files import ProjectFileAccess, ProjectFileError
from codex_remote_mcp_git_diff import build_repo_diff
from codex_remote_mcp_redaction import redact
from simdorei_mcp_common.operation_outputs import (
    GitCommitOutput,
    GitPushOutput,
    RepoDiffOutput,
    RepoStatusOutput,
)
from simdorei_mcp_common.operation_requests import GitCommitRequest, GitPushRequest

MAX_GIT_OUTPUT: Final = 200_000
MAX_GIT_CAPTURE_BYTES: Final = 400_000
GIT_TIMEOUT_SECONDS: Final = 120
GIT_ENVIRONMENT_KEYS: Final = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "PATHEXT",
    "LOCALAPPDATA",
    "APPDATA",
    "USERPROFILE",
    "SSH_AUTH_SOCK",
)
SAFE_CREDENTIAL_HELPER: Final = re.compile(
    r"^[A-Za-z0-9._/+:-]+(?:\s+--?[A-Za-z0-9._/=:+-]+)*$"
)


class ProjectGitError(ProjectFileError):
    """Raised when a confined Git operation fails."""


def repo_status(root: Path) -> RepoStatusOutput:
    """Read local repository state without contacting a remote."""
    branch = _run_git(root, ("branch", "--show-current")).stdout.strip()
    porcelain = _run_git(root, ("status", "--porcelain=v1")).stdout
    dirty, staged = _parse_status(porcelain)
    dirty = _visible_paths(root, dirty)
    staged = _visible_paths(root, staged)
    remotes = tuple(
        line.strip()
        for line in _run_git(root, ("remote",)).stdout.splitlines()
        if line.strip()
    )
    upstream_result = _try_git(
        root,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
    )
    upstream = upstream_result.stdout.strip() if upstream_result.returncode == 0 else None
    ahead, behind = _ahead_behind(root, upstream)
    return RepoStatusOutput(
        branch=branch,
        dirty_files=dirty,
        staged_files=staged,
        remotes=remotes,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
    )


def repo_diff(root: Path) -> RepoDiffOutput:
    """Return a bounded HEAD-relative patch and numeric summary."""
    return build_repo_diff(root, _run_git)


def git_commit(root: Path, request: GitCommitRequest) -> GitCommitOutput:
    """Stage validated paths and create one local commit."""
    access = ProjectFileAccess(root)
    paths = request.paths
    if not paths:
        raise ProjectGitError(
            "<git>",
            "explicit paths are required to avoid committing unrelated work",
        )
    for path in paths:
        _ = access.resolve_path(path, require_file=False)
    untracked = {
        line.strip()
        for line in _run_git(
            root,
            ("ls-files", "--others", "--exclude-standard"),
        ).stdout.splitlines()
        if line.strip()
    }
    new_paths = tuple(path for path in paths if path in untracked)
    if new_paths:
        _run_git(root, ("add", "--intent-to-add", "--", *new_paths))
    _run_git(
        root,
        ("commit", "--only", "-m", request.message, "--", *paths),
    )
    commit = _run_git(root, ("rev-parse", "--short", "HEAD")).stdout.strip()
    branch = _run_git(root, ("branch", "--show-current")).stdout.strip()
    committed = tuple(
        line.strip()
        for line in _run_git(
            root,
            ("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"),
        ).stdout.splitlines()
        if line.strip()
    )
    return GitCommitOutput(
        commit=commit,
        branch=branch,
        staged_files=committed,
    )


def git_push(root: Path, request: GitPushRequest) -> GitPushOutput:
    """Push the selected local branch to one configured Git remote."""
    remotes = {
        line.strip()
        for line in _run_git(root, ("remote",)).stdout.splitlines()
        if line.strip()
    }
    if request.remote not in remotes:
        raise ProjectGitError(request.remote, "Git remote is not configured")
    branch = request.branch or _run_git(
        root,
        ("branch", "--show-current"),
    ).stdout.strip()
    if not branch:
        raise ProjectGitError("<git>", "detached HEAD cannot be pushed implicitly")
    result = _run_git(
        root,
        ("push", "-u", "--", request.remote, branch),
        timeout_seconds=300,
        allow_credentials=True,
    )
    combined = f"{result.stdout}\n{result.stderr}".strip()
    return GitPushOutput(
        remote=request.remote,
        branch=branch,
        output=redact(combined)[:MAX_GIT_OUTPUT],
    )


def _changed_paths(root: Path) -> tuple[str, ...]:
    status = _run_git(root, ("status", "--porcelain=v1")).stdout
    dirty, staged = _parse_status(status)
    return tuple(dict.fromkeys((*staged, *dirty)))


def _parse_status(status: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dirty: list[str] = []
    staged: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        index_state = line[0]
        worktree_state = line[1]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", maxsplit=1)[1]
        if index_state == "?" and worktree_state == "?":
            dirty.append(path)
            continue
        if index_state not in {" ", "?"}:
            staged.append(path)
        if worktree_state not in {" ", "?"}:
            dirty.append(path)
    return tuple(dirty), tuple(staged)


def _ahead_behind(root: Path, upstream: str | None) -> tuple[int, int]:
    if upstream is None:
        return 0, 0
    result = _try_git(root, ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"))
    if result.returncode != 0:
        return 0, 0
    values = result.stdout.strip().split()
    if len(values) != 2:
        return 0, 0
    return int(values[0]), int(values[1])


def _run_git(
    root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: int = GIT_TIMEOUT_SECONDS,
    allow_credentials: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = _try_git(
        root,
        arguments,
        timeout_seconds=timeout_seconds,
        allow_credentials=allow_credentials,
    )
    if result.returncode != 0:
        message = redact(result.stderr.strip() or result.stdout.strip())
        raise ProjectGitError("<git>", message[:4_000])
    return result


def _try_git(
    root: Path,
    arguments: tuple[str, ...],
    *,
    timeout_seconds: int = GIT_TIMEOUT_SECONDS,
    allow_credentials: bool = False,
) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git") or "git"
    command_parts = [
        git,
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "credential.helper=",
    ]
    helpers = _trusted_credential_helpers(git) if allow_credentials else ()
    if allow_credentials:
        for helper in helpers:
            command_parts.extend(("-c", f"credential.helper={helper}"))
    command_parts.extend(arguments)
    command = tuple(command_parts)
    with tempfile.NamedTemporaryFile(
        prefix=".codex-remote-git-",
        suffix=".config",
        delete=False,
    ) as policy_file:
        policy_path = Path(policy_file.name)
    try:
        try:
            _write_git_policy(policy_path, root, helpers)
        except OSError as exc:
            raise ProjectGitError("<git>", "Git policy file could not be written") from exc
        environment = _safe_git_environment()
        environment["GIT_CONFIG_GLOBAL"] = str(policy_path)
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            completed = subprocess.run(
                command,
                cwd=root,
                env=environment,
                check=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout_seconds,
            )
            stdout = _read_git_output(stdout_file)
            stderr = _read_git_output(stderr_file)
        return subprocess.CompletedProcess(
            args=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    except FileNotFoundError as exc:
        raise ProjectGitError("<git>", "Git executable was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProjectGitError("<git>", "Git command timed out") from exc
    finally:
        policy_path.unlink(missing_ok=True)


def _safe_git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key in GIT_ENVIRONMENT_KEYS
        if (value := os.environ.get(key)) is not None
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    ssh = shutil.which("ssh")
    if ssh is not None:
        environment["GIT_SSH_COMMAND"] = f'"{ssh}"'
        environment["GIT_SSH_VARIANT"] = "ssh"
    true_command = shutil.which("true")
    if true_command is not None:
        environment["GIT_ASKPASS"] = true_command
        environment["SSH_ASKPASS"] = true_command
    return environment


def _read_git_output(handle) -> str:
    handle.seek(0)
    return handle.read(MAX_GIT_CAPTURE_BYTES).decode(
        "utf-8",
        errors="replace",
    )


def _trusted_credential_helpers(git: str) -> tuple[str, ...]:
    helpers: list[str] = []
    for scope in ("--system", "--global"):
        result = subprocess.run(
            (git, "config", scope, "--get-all", "credential.helper"),
            env=_safe_git_environment(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if result.returncode not in (0, 1):
            continue
        for helper in result.stdout.splitlines():
            value = helper.strip()
            if (
                value
                and not value.startswith("!")
                and SAFE_CREDENTIAL_HELPER.fullmatch(value) is not None
            ):
                helpers.append(value)
    return tuple(dict.fromkeys(helpers))


def _write_git_policy(
    path: Path,
    root: Path,
    helpers: tuple[str, ...],
) -> None:
    lines = (
        "[safe]",
        f"\tdirectory = {root.as_posix()}",
        "[credential]",
        "\thelper =",
        *(f"\thelper = {helper}" for helper in helpers),
        "",
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _visible_paths(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    access = ProjectFileAccess(root)
    visible: list[str] = []
    for path in paths:
        try:
            _ = access.resolve_path(path, require_file=False)
        except ProjectFileError:
            continue
        visible.append(path)
    return tuple(visible)
