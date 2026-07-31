from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import final

from codex_remote_mcp_computer import ComputerController
from codex_remote_mcp_computer_errors import ComputerControlError
from codex_remote_mcp_files import ProjectFileAccess
from codex_remote_mcp_idempotency import IdempotentResultCache


@dataclass(frozen=True, slots=True)
class ActiveProject:
    access: ProjectFileAccess
    expires_at: datetime
    result_cache: IdempotentResultCache = field(
        default_factory=IdempotentResultCache,
        compare=False,
        repr=False,
    )
    execution_lock: threading.RLock = field(
        default_factory=threading.RLock,
        compare=False,
        repr=False,
    )


@final
class ProjectDispatchState:  # MUTABLE_OK: synchronized project/session registry.
    def __init__(
        self,
        computer_factory: Callable[[], ComputerController],
    ) -> None:
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._projects: dict[str, ActiveProject] = {}
        self._computer_factory = computer_factory
        self._computers: dict[str, ComputerController] = {}
        self._sessions: dict[str, str] = {}
        self._computer_stopped: set[str] = set()

    def binding(self, thread_id: str) -> ActiveProject | None:
        with self._lock:
            return self._projects.get(thread_id)

    def upsert(self, thread_id: str, root: Path, expires_at: datetime) -> None:
        project = ActiveProject(access=ProjectFileAccess(root), expires_at=expires_at)
        with self._lifecycle_lock:
            previous_project = self.binding(thread_id)
            if previous_project is None:
                self._replace(thread_id, project)
            else:
                with previous_project.execution_lock:
                    self._replace(thread_id, project)

    def computer_for(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        session_id: str | None,
    ) -> ComputerController | None:
        with self._lock:
            if thread_id in self._computer_stopped:
                return None
            self._require_locked(thread_id, expected_project, session_id)
            current = self._computers.get(thread_id)
            if current is not None:
                return current
            created = self._computer_factory()
            self._computers[thread_id] = created
            return created

    def activate(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        session_id: str,
    ) -> None:
        with self._lock:
            if self._projects.get(thread_id) is not expected_project:
                raise ComputerControlError(
                    "The project binding changed while the command was starting."
                )
            if self._sessions.get(thread_id) == session_id:
                return
            previous = self._computers.get(thread_id)
        if previous is not None:
            previous.stop()
        with self._lock:
            if self._projects.get(thread_id) is not expected_project:
                raise ComputerControlError(
                    "The project binding changed while the command was starting."
                )
            if self._computers.get(thread_id) is previous:
                _ = self._computers.pop(thread_id, None)
            self._sessions[thread_id] = session_id

    def require(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        session_id: str | None,
    ) -> None:
        if session_id is None:
            raise ComputerControlError(
                "The project command has no active ChatGPT session."
            )
        with self._lock:
            self._require_locked(thread_id, expected_project, session_id)

    def is_computer_stopped(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self._computer_stopped

    def stop_computer(self, thread_id: str) -> None:
        with self._lock:
            self._computer_stopped.add(thread_id)
            controller = self._computers.get(thread_id)
        if controller is not None:
            controller.stop()

    def stop_computer_if_bound(
        self,
        thread_id: str,
        expected_project: ActiveProject,
    ) -> None:
        with self._lock:
            if self._projects.get(thread_id) is not expected_project:
                raise ComputerControlError(
                    "The project binding changed while the command was starting."
                )
            self._computer_stopped.add(thread_id)
            controller = self._computers.get(thread_id)
        if controller is not None:
            controller.stop()

    def stop_computer_if_current(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        session_id: str | None,
    ) -> None:
        if session_id is None:
            raise ComputerControlError(
                "The project command has no active ChatGPT session."
            )
        with self._lock:
            self._require_locked(thread_id, expected_project, session_id)
            self._computer_stopped.add(thread_id)
            controller = self._computers.get(thread_id)
        if controller is not None:
            controller.stop()

    def invalidate_sessions(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                project_locks = tuple(
                    project.execution_lock
                    for _, project in sorted(self._projects.items())
                )
            for project_lock in project_locks:
                _ = project_lock.acquire()
            try:
                with self._lock:
                    controllers = tuple(self._computers.items())
                    self._sessions.clear()
                failures: list[ComputerControlError] = []
                for thread_id, controller in controllers:
                    try:
                        controller.stop()
                    except ComputerControlError as exc:
                        failures.append(exc)
                        continue
                    with self._lock:
                        if self._computers.get(thread_id) is controller:
                            del self._computers[thread_id]
                if failures:
                    raise failures[0]
            finally:
                for project_lock in reversed(project_locks):
                    project_lock.release()

    def _replace(self, thread_id: str, project: ActiveProject) -> None:
        with self._lock:
            previous = self._computers.get(thread_id)
        if previous is not None:
            previous.stop()
        with self._lock:
            self._projects[thread_id] = project
            if self._computers.get(thread_id) is previous:
                _ = self._computers.pop(thread_id, None)
            _ = self._sessions.pop(thread_id, None)
            self._computer_stopped.discard(thread_id)

    def _require_locked(
        self,
        thread_id: str,
        expected_project: ActiveProject,
        session_id: str | None,
    ) -> None:
        if self._projects.get(thread_id) is not expected_project:
            raise ComputerControlError(
                "The project binding changed while the command was starting."
            )
        if self._sessions.get(thread_id) != session_id:
            raise ComputerControlError(
                "The ChatGPT session is stale or was not acknowledged locally."
            )
