from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class QuarantineFence:
    generation: int
    process: object | None
    process_id: int | None
    started_at: float
    deadline: float
    fenced: bool = False

    def with_deadline(self, deadline: float) -> QuarantineFence:
        return replace(self, deadline=deadline)

    def activate(self) -> QuarantineFence:
        return replace(self, fenced=True)

    def expired(self, now: float) -> bool:
        return now >= self.deadline

    def matches(self, process: object | None, generation: int) -> bool:
        return self.process is process and self.generation == generation


@dataclass(frozen=True, slots=True)
class IsolatedTurnProbe:
    generation: int
    process: object | None
    process_id: int | None
    thread_id: str
    turn_id: str

    def matches(
        self,
        *,
        process: object | None,
        generation: int,
        thread_id: str,
        turn_id: str | None,
    ) -> bool:
        return (
            self.process is process
            and self.generation == generation
            and self.thread_id == thread_id
            and self.turn_id == turn_id
        )


def make_isolated_turn_probe(
    *,
    generation: int,
    process: object | None,
    thread_id: str,
    turn_id: str,
) -> IsolatedTurnProbe:
    process_id = getattr(process, "pid", None)
    return IsolatedTurnProbe(
        generation=generation,
        process=process,
        process_id=int(process_id) if isinstance(process_id, int) else None,
        thread_id=thread_id,
        turn_id=turn_id,
    )


def begin_quarantine(
    *,
    generation: int,
    process: object | None,
    now: float,
    hard_cap_seconds: float,
    existing: QuarantineFence | None,
) -> QuarantineFence:
    if existing is not None and existing.matches(process, generation):
        return existing
    process_id = getattr(process, "pid", None)
    return QuarantineFence(
        generation=generation,
        process=process,
        process_id=int(process_id) if isinstance(process_id, int) else None,
        started_at=now,
        deadline=now + hard_cap_seconds,
    )
