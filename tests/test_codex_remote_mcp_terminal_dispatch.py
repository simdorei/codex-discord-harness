from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from codex_remote_mcp_dispatch import LocalProjectDispatcher
from simdorei_mcp_common.messages import (
    OperationErrorResult,
    ProjectOperationCommand,
    ProjectOperationResult,
    ProjectSessionCommand,
    ProjectSessionResult,
    RequestId,
)
from simdorei_mcp_common.terminal_protocol import (
    TerminalExecOutput,
    TerminalExecRequest,
)


def _python(code: str) -> str:
    if os.name != "nt":
        return shlex.join([sys.executable, "-c", code])
    executable = sys.executable.replace("'", "''")
    powershell_code = code.replace("'", "''")
    return f"& '{executable}' -c '{powershell_code}'"


def _dispatcher(root: Path) -> LocalProjectDispatcher:
    dispatcher = LocalProjectDispatcher()
    dispatcher.upsert(
        "thread-a",
        root,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    activated = dispatcher.execute(
        ProjectSessionCommand(
            request_id=RequestId("activate-session-one"),
            thread_id="thread-a",
            computer_session_id="session-one-generation",
        )
    )
    assert isinstance(activated, ProjectSessionResult)
    return dispatcher


def _command(
    request_id: str,
    operation: TerminalExecRequest,
    *,
    session_id: str = "session-one-generation",
) -> ProjectOperationCommand:
    return ProjectOperationCommand(
        request_id=RequestId(request_id),
        thread_id="thread-a",
        computer_session_id=session_id,
        operation=operation,
    )


def _terminal_output(result: object) -> TerminalExecOutput:
    assert isinstance(result, ProjectOperationResult)
    assert isinstance(result.output, TerminalExecOutput)
    return result.output


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists()


def test_dispatch_executes_and_reuses_a_session_owned_terminal(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    first = _terminal_output(
        dispatcher.execute(
            _command(
                "terminal-first",
                TerminalExecRequest(command=_python("print('dispatch-ok')")),
            )
        )
    )
    second = _terminal_output(
        dispatcher.execute(
            _command(
                "terminal-second",
                TerminalExecRequest(
                    terminal_id=first.terminal_id,
                    command="echo reused",
                ),
            )
        )
    )

    assert first.exit_code == 0
    assert "dispatch-ok" in first.stdout
    assert second.exit_code == 0
    assert "reused" in second.stdout
    assert second.terminal_id == first.terminal_id


def test_project_session_replacement_cancels_owned_terminal_work(
    tmp_path: Path,
) -> None:
    dispatcher = _dispatcher(tmp_path)
    seed = _terminal_output(
        dispatcher.execute(
            _command("terminal-seed", TerminalExecRequest(command="echo seed"))
        )
    )
    marker = tmp_path / "session-replace-started"
    outcomes: list[object] = []
    worker = threading.Thread(
        target=lambda: outcomes.append(
            dispatcher.execute(
                _command(
                    "terminal-long",
                    TerminalExecRequest(
                        terminal_id=seed.terminal_id,
                        command=_python(
                            "from pathlib import Path; import time; "
                            + f"Path({str(marker)!r}).write_text('x'); time.sleep(30)"
                        ),
                        timeout_seconds=60,
                    ),
                )
            )
        )
    )
    worker.start()
    _wait_for(marker)

    activated = dispatcher.execute(
        ProjectSessionCommand(
            request_id=RequestId("activate-session-two"),
            thread_id="thread-a",
            computer_session_id="session-two-generation",
        )
    )
    worker.join(timeout=10)

    assert isinstance(activated, ProjectSessionResult)
    assert not worker.is_alive()
    assert _terminal_output(outcomes[0]).cancelled is True


def test_connection_invalidation_cancels_terminal_process_tree(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    marker = tmp_path / "connection-invalidate-started"
    outcomes: list[object] = []
    worker = threading.Thread(
        target=lambda: outcomes.append(
            dispatcher.execute(
                _command(
                    "connection-long",
                    TerminalExecRequest(
                        command=_python(
                            "from pathlib import Path; import time; "
                            + f"Path({str(marker)!r}).write_text('x'); time.sleep(30)"
                        ),
                        timeout_seconds=60,
                    ),
                )
            )
        )
    )
    worker.start()
    _wait_for(marker)

    dispatcher.invalidate_computer_sessions()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert _terminal_output(outcomes[0]).cancelled is True


def test_invalidation_cannot_overtake_terminal_registration(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    registration_entered = threading.Event()
    release_registration = threading.Event()
    invalidation_completed = threading.Event()
    outcomes: list[object] = []
    original_for_session = dispatcher._terminals.for_session  # pyright: ignore[reportPrivateUsage]

    def delayed_for_session(thread_id: str, root: Path, session_id: str):
        registration_entered.set()
        assert release_registration.wait(5)
        return original_for_session(thread_id, root, session_id)

    with patch.object(
        dispatcher._terminals,  # pyright: ignore[reportPrivateUsage]
        "for_session",
        side_effect=delayed_for_session,
    ):
        worker = threading.Thread(
            target=lambda: outcomes.append(
                dispatcher.execute(
                    _command(
                        "registration-race",
                        TerminalExecRequest(command="echo registration-race"),
                    )
                )
            )
        )
        worker.start()
        assert registration_entered.wait(5)
        invalidation = threading.Thread(
            target=lambda: (
                dispatcher.invalidate_computer_sessions(),
                invalidation_completed.set(),
            )
        )
        invalidation.start()
        assert not invalidation_completed.wait(0.2)

        release_registration.set()
        worker.join(timeout=10)
        invalidation.join(timeout=10)

    assert not worker.is_alive()
    assert not invalidation.is_alive()
    assert invalidation_completed.is_set()
    assert len(outcomes) == 1


def test_terminal_failure_uses_terminal_execution_error_code(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    result = dispatcher.execute(
        _command(
            "foreign-terminal",
            TerminalExecRequest(
                terminal_id="term_0123456789abcdef",
                command="echo rejected",
            ),
        )
    )

    assert isinstance(result, OperationErrorResult)
    assert result.error_code == "terminal_execution"
