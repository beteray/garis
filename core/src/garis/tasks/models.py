"""Task records."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..agent.goal import Goal, Plan


class TaskState(StrEnum):
    PENDING = "pending"      # accepted, waiting for a slot
    RUNNING = "running"
    BLOCKED = "blocked"      # waiting for the user: an approval or an answer
    FINISHED = "finished"
    FAILED = "failed"
    STOPPED = "stopped"      # user pulled the plug

    @property
    def terminal(self) -> bool:
        return self in (TaskState.FINISHED, TaskState.FAILED, TaskState.STOPPED)

    @property
    def active(self) -> bool:
        return self in (TaskState.PENDING, TaskState.RUNNING, TaskState.BLOCKED)


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class TaskRecord:
    id: str
    goal: str
    state: TaskState = TaskState.PENDING
    criteria: tuple[str, ...] = ()
    origin: str = "user"             # user | automation | proactive | remote
    target: str = "local"
    plan: dict[str, Any] = field(default_factory=dict)
    cursor: int = 0
    result: Any = None
    report: dict[str, Any] | None = None
    error: str = ""
    attempts: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    deadline: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        goal: str,
        *,
        criteria: tuple[str, ...] = (),
        origin: str = "user",
        target: str = "local",
        deadline: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TaskRecord:
        return cls(
            id=uuid.uuid4().hex[:16],
            goal=goal.strip(),
            criteria=criteria,
            origin=origin,
            target=target,
            deadline=deadline,
            meta=meta or {},
        )

    def to_goal(self) -> Goal:
        return Goal(
            text=self.goal,
            criteria=self.criteria,
            target=self.target,
            private=bool(self.meta.get("private")),
            context=str(self.meta.get("context", "")),
            origin=self.origin,
        )

    def plan_object(self) -> Plan:
        return Plan.from_dict(self.plan) if self.plan else Plan()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def expired(self) -> bool:
        return self.deadline is not None and self.deadline < time.time()

    def to_dict(self, *, include_plan: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "goal": self.goal,
            "state": self.state.value,
            "criteria": list(self.criteria),
            "origin": self.origin,
            "target": self.target,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "attempts": self.attempts,
        }
        if self.report:
            data["report"] = self.report
        if include_plan:
            data["plan"] = self.plan
            data["result"] = self.result
        return data

    def summary_line(self) -> str:
        marks = {
            TaskState.PENDING: "…",
            TaskState.RUNNING: "▶",
            TaskState.BLOCKED: "?",
            TaskState.FINISHED: "✓",
            TaskState.FAILED: "✗",
            TaskState.STOPPED: "■",
        }
        goal = self.goal if len(self.goal) <= 70 else self.goal[:69] + "…"
        return f"{marks[self.state]} {self.id}  {goal}"


@dataclass(slots=True)
class StepRecord:
    task_id: str
    step_key: str
    ordinal: int
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    state: StepState = StepState.PENDING
    result: Any = None
    error: str = ""
    attempts: int = 0
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_key": self.step_key,
            "ordinal": self.ordinal,
            "tool": self.tool,
            "state": self.state.value,
            "error": self.error,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


__all__ = ["StepRecord", "StepState", "TaskRecord", "TaskState"]
