"""Durable task storage.

This is the entire mechanism behind "a task survives a reboot": the plan and
every finished step are rows in SQLite before the agent loop moves on, so
:class:`TaskSupervisor` can rebuild exactly where a task stood after the process
restarts — without replaying steps that already ran.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from ..agent.goal import Plan
from ..runtime import ActionResult
from ..store import Database, dumps, loads
from .models import StepRecord, StepState, TaskRecord, TaskState


class TaskStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ tasks

    def create(self, task: TaskRecord) -> TaskRecord:
        self.db.execute(
            "INSERT INTO tasks(id, goal, criteria, state, origin, target, plan, cursor,"
            " result, report, error, attempts, created_at, updated_at, started_at,"
            " finished_at, deadline, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task.id, task.goal, dumps(list(task.criteria)), task.state.value,
                task.origin, task.target, dumps(task.plan), task.cursor,
                dumps(task.result), dumps(task.report), task.error, task.attempts,
                task.created_at, task.updated_at, task.started_at, task.finished_at,
                task.deadline, dumps(task.meta),
            ),
        )
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        row = self.db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _row_to_task(row) if row else None

    def require(self, task_id: str) -> TaskRecord:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def list(
        self,
        *,
        state: TaskState | str | None = None,
        active_only: bool = False,
        limit: int = 200,
    ) -> Sequence[TaskRecord]:
        sql = "SELECT * FROM tasks WHERE 1 = 1"
        params: list[Any] = []
        if state is not None:
            sql += " AND state = ?"
            params.append(TaskState(state).value)
        if active_only:
            sql += " AND state IN (?, ?, ?)"
            params += [TaskState.PENDING.value, TaskState.RUNNING.value,
                       TaskState.BLOCKED.value]
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_row_to_task(r) for r in self.db.query(sql, params)]

    def set_state(self, task_id: str, state: TaskState, **extra: Any) -> None:
        fields = {"state": state.value, "updated_at": time.time(), **extra}
        if state is TaskState.RUNNING and "started_at" not in fields:
            fields.setdefault("started_at", time.time())
        if state.terminal:
            fields.setdefault("finished_at", time.time())
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?",
            (*_encode_values(fields), task_id),
        )

    def save_plan(self, task_id: str, plan: Plan) -> None:
        self.db.execute(
            "UPDATE tasks SET plan = ?, updated_at = ? WHERE id = ?",
            (dumps(plan.to_dict()), time.time(), task_id),
        )

    def finish(
        self,
        task_id: str,
        *,
        state: TaskState,
        report: dict[str, Any] | None = None,
        result: Any = None,
        error: str = "",
    ) -> None:
        self.set_state(
            task_id, state,
            report=dumps(report), result=dumps(result), error=error,
        )

    def touch(self, task_id: str) -> None:
        self.db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (time.time(), task_id))

    def bump_attempts(self, task_id: str) -> int:
        self.db.execute(
            "UPDATE tasks SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
            (time.time(), task_id),
        )
        row = self.db.one("SELECT attempts FROM tasks WHERE id = ?", (task_id,))
        return row["attempts"] if row else 0

    def prune_finished(self, older_than_days: int) -> int:
        cutoff = time.time() - older_than_days * 86400
        cur = self.db.execute(
            "DELETE FROM tasks WHERE state IN (?, ?, ?) AND finished_at IS NOT NULL"
            " AND finished_at < ?",
            (TaskState.FINISHED.value, TaskState.FAILED.value, TaskState.STOPPED.value, cutoff),
        )
        return cur.rowcount or 0

    # -------------------------------------------------------------------- steps

    def step_started(self, task_id: str, step_key: str, ordinal: int, tool: str,
                     params: dict[str, Any]) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO task_steps(task_id, step_key, ordinal, tool, params, state,"
            " attempts, started_at) VALUES (?,?,?,?,?,?,1,?)"
            " ON CONFLICT(task_id, step_key) DO UPDATE SET"
            " attempts = task_steps.attempts + 1, started_at = excluded.started_at,"
            " state = excluded.state",
            (task_id, step_key, ordinal, tool, dumps(params), StepState.RUNNING.value, now),
        )

    def step_finished(self, task_id: str, step_key: str, result: ActionResult) -> None:
        state = StepState.DONE if result.ok else StepState.FAILED
        self.db.execute(
            "UPDATE task_steps SET state = ?, result = ?, error = ?, finished_at = ?"
            " WHERE task_id = ? AND step_key = ?",
            (state.value, dumps(result.value), result.error or "", time.time(),
             task_id, step_key),
        )

    def completed_steps(self, task_id: str) -> dict[str, Any]:
        """``{step_key: result_value}`` for every step that finished successfully.

        This is what makes resumption idempotent: the loop checks this map before
        running a step and skips anything already here.
        """
        rows = self.db.query(
            "SELECT step_key, result FROM task_steps WHERE task_id = ? AND state = ?",
            (task_id, StepState.DONE.value),
        )
        return {r["step_key"]: loads(r["result"]) for r in rows}

    def steps_for(self, task_id: str) -> Sequence[StepRecord]:
        rows = self.db.query(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY ordinal", (task_id,)
        )
        return [_row_to_step(r) for r in rows]

    def reset_running_steps(self, task_id: str) -> int:
        """On resume, a step caught mid-flight goes back to pending, not done."""
        cur = self.db.execute(
            "UPDATE task_steps SET state = ? WHERE task_id = ? AND state = ?",
            (StepState.PENDING.value, task_id, StepState.RUNNING.value),
        )
        return cur.rowcount or 0

    def recover_incomplete(self) -> Sequence[TaskRecord]:
        """Tasks left RUNNING when the process died. Called once at startup."""
        rows = self.db.query(
            "SELECT * FROM tasks WHERE state = ?", (TaskState.RUNNING.value,)
        )
        tasks = [_row_to_task(r) for r in rows]
        for task in tasks:
            self.reset_running_steps(task.id)
        return tasks


def _encode_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    encoded = []
    for key, value in fields.items():
        if key in ("result", "report", "meta") and not isinstance(value, str):
            encoded.append(dumps(value))
        else:
            encoded.append(value)
    return tuple(encoded)


def _row_to_task(row: Any) -> TaskRecord:
    return TaskRecord(
        id=row["id"],
        goal=row["goal"],
        state=TaskState(row["state"]),
        criteria=tuple(loads(row["criteria"], [])),
        origin=row["origin"],
        target=row["target"],
        plan=loads(row["plan"], {}),
        cursor=row["cursor"],
        result=loads(row["result"]),
        report=loads(row["report"]),
        error=row["error"] or "",
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        deadline=row["deadline"],
        meta=loads(row["meta"], {}),
    )


def _row_to_step(row: Any) -> StepRecord:
    return StepRecord(
        task_id=row["task_id"],
        step_key=row["step_key"],
        ordinal=row["ordinal"],
        tool=row["tool"],
        params=loads(row["params"], {}),
        state=StepState(row["state"]),
        result=loads(row["result"]),
        error=row["error"] or "",
        attempts=row["attempts"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


__all__ = ["TaskStore"]
