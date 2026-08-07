"""Task supervisor: concurrent, equally-important, durable.

Three product requirements meet here:

* **Tasks run for minutes, hours or days** and are continued after the app or the
  machine restarts — ``recover`` re-attaches to anything left ``RUNNING`` when the
  process died.
* **Many tasks run at once**, all equally important by default; a semaphore
  bounds parallelism (``TasksConfig.max_parallel``) and contention on a shared
  file, program or device is resolved by the lease manager, not by task priority.
* **Silence.** The supervisor drives the agent loop and lets its events speak;
  it does not narrate scheduling to the user.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..agent import AgentLoop, AgentState, Outcome, Planner, Verifier
from ..config import Config
from ..errors import TaskAborted
from ..events import EventBus, Topic
from ..kernel.recovery import RecoveryStatus
from ..runtime import ActionResult, Runtime
from ..runtime.recovery import EffectRecoveryService
from ..store import dumps
from .models import StepRecord, TaskRecord, TaskState
from .store import TaskStore


@dataclass(slots=True)
class _StoreJournal:
    """Adapts :class:`TaskStore` to the narrower interface the agent loop needs."""

    store: TaskStore

    def save_plan(self, task_id: str, plan: Any) -> None:
        self.store.save_plan(task_id, plan)

    def completed_steps(self, task_id: str) -> dict[str, Any]:
        return self.store.completed_steps(task_id)

    def step_started(self, task_id: str, step: Any, ordinal: int) -> None:
        self.store.step_started(task_id, step.key, ordinal, step.target, step.params)

    def step_finished(self, task_id: str, step: Any, result: ActionResult) -> None:
        self.store.step_finished(task_id, step.key, result)


class TaskSupervisor:
    def __init__(
        self,
        store: TaskStore,
        runtime: Runtime,
        planner: Planner,
        verifier: Verifier,
        *,
        bus: EventBus,
        config: Config,
        recovery: EffectRecoveryService | None = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = config
        #: Optional so every existing caller keeps working. Without it a resumed
        #: task simply proceeds as before — it still cannot repeat an uncertain
        #: effect, because `EffectRecord.repeatable` refuses the reservation.
        self.recovery = recovery
        self.loop = AgentLoop(
            runtime, planner, verifier, bus=bus, config=config,
            journal=_StoreJournal(store),
        )
        self._semaphore = asyncio.Semaphore(max(1, config.tasks.max_parallel))
        self._running: dict[str, asyncio.Task[None]] = {}
        self._stop_flags: set[str] = set()

    # ------------------------------------------------------------------ submit

    def submit(
        self,
        goal: str,
        *,
        criteria: tuple[str, ...] = (),
        origin: str = "user",
        target: str = "local",
        deadline: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> TaskRecord:
        task = TaskRecord.new(
            goal, criteria=criteria, origin=origin, target=target,
            deadline=deadline, meta=meta,
        )
        self.store.create(task)
        self.bus.emit(Topic.TASK_CREATED, task_id=task.id, goal=task.goal, origin=origin)
        self._launch(task.id)
        return task

    def resume(self, task_id: str) -> TaskRecord:
        """Re-enter a BLOCKED task, e.g. after an approval or a missing answer arrives.

        The state flips to PENDING synchronously, before the coroutine is scheduled.
        Otherwise the task would still read as BLOCKED to everything watching it —
        the UI, the API, ``wait()`` — until the event loop happened to get around
        to it, which looks exactly like an approval that did nothing.
        """
        task = self.store.require(task_id)
        if task.state not in (TaskState.BLOCKED, TaskState.PENDING):
            return task
        self.store.set_state(task_id, TaskState.PENDING, error="")
        self._launch(task_id)
        return self.store.require(task_id)

    def stop(self, task_id: str) -> bool:
        """Ask a running task to stop. Cooperative: it stops between steps, not mid-write."""
        running = self._running.get(task_id)
        if running is not None and not running.done():
            self._stop_flags.add(task_id)
            running.cancel()
            return True
        task = self.store.get(task_id)
        if task is not None and task.state.active:
            self.store.set_state(task_id, TaskState.STOPPED, error="zatrzymane przez użytkownika")
            return True
        return False

    # --------------------------------------------------------------- lifecycle

    def _launch(self, task_id: str) -> None:
        if task_id in self._running and not self._running[task_id].done():
            return
        self._running[task_id] = asyncio.ensure_future(self._run(task_id))

    async def _run(self, task_id: str) -> None:
        async with self._semaphore:
            task = self.store.require(task_id)
            self.store.set_state(task_id, TaskState.RUNNING)
            self.bus.emit(Topic.TASK_STARTED, task_id=task_id, goal=task.goal)

            try:
                outcome = await self.loop.run(task.to_goal(), task_id=task_id)
            except asyncio.CancelledError:
                self.store.set_state(
                    task_id, TaskState.STOPPED, error="zatrzymane przez użytkownika"
                )
                self.bus.emit(Topic.TASK_STOPPED, task_id=task_id)
                return
            except TaskAborted as exc:
                self.store.set_state(task_id, TaskState.STOPPED, error=str(exc))
                self.bus.emit(Topic.TASK_STOPPED, task_id=task_id)
                return
            finally:
                self._stop_flags.discard(task_id)

            self._apply_outcome(task_id, outcome)

    def _apply_outcome(self, task_id: str, outcome: Outcome) -> None:
        if outcome.state is AgentState.BLOCKED:
            # Persist the question, not just the state: whatever asks later — the
            # CLI, the tray, the phone — needs to know what GARIS is waiting for,
            # and the in-memory outcome is gone after a restart.
            self.store.set_state(
                task_id,
                TaskState.BLOCKED,
                report=dumps(outcome.report.to_dict()),
                error=outcome.question,
            )
            self.bus.emit(
                Topic.TASK_BLOCKED,
                task_id=task_id,
                question=outcome.question,
                approval_id=outcome.approval_id,
            )
            return

        final = TaskState.FINISHED if outcome.ok else TaskState.FAILED
        self.store.finish(
            task_id,
            state=final,
            report=outcome.report.to_dict(),
            result=outcome.report.developer,
            error="" if outcome.ok else (outcome.report.short or "nie udało się"),
        )
        topic = Topic.TASK_FINISHED if outcome.ok else Topic.TASK_FAILED
        self.bus.emit(topic, task_id=task_id, report=outcome.report.short)

    # ------------------------------------------------------------------ recover

    async def recover(self) -> Sequence[TaskRecord]:
        """Re-attach to work interrupted by a crash or a reboot.

        Anything left RUNNING did not choose to stop; its steps already on disk
        are kept, its unfinished step is retried, and it resumes as if nothing
        happened. Anything BLOCKED stays put — it is correctly waiting for the
        user, restarting it would only repeat the same question.

        Before any of that, uncertain effects get looked at. A task that died
        mid-action must not be handed back to the loop while nobody knows
        whether its last step touched the world — the loop's job is to make
        progress, and progress over an unresolved effect is the repeat this
        whole layer exists to prevent. Recovery only ever *inspects*; a task it
        cannot settle stays blocked instead of resuming.
        """
        recovered = self.store.recover_incomplete()
        for task in recovered:
            if not await self._settle_uncertain(task):
                self._block(task, "Nie wiem, czy poprzednia operacja doszła do skutku.")
                continue
            self.bus.emit(Topic.TASK_STARTED, task_id=task.id, goal=task.goal, resumed=True)
            self._launch(task.id)
        return recovered

    async def _settle_uncertain(self, task: TaskRecord) -> bool:
        """Whether this task may resume. Inspects; never repeats.

        Returns False when anything is left that only a person can decide. The
        verdict comes from the deterministic reconciler and is not open to
        revision by the goal-level verifier later — a model saying "looks done"
        must not overturn a measurement.
        """
        if self.recovery is None:
            return True
        for effect in self.recovery.uncertain(task.id):
            if not self.recovery.can_recover(effect):
                return False
            outcome = await self.recovery.reconcile(effect.effect_id)
            if not outcome.resolved:
                return False
            if outcome.status is RecoveryStatus.RESOLVED_NOT_APPLIED:
                # Proven untouched, and reached second-hand. The step may be
                # done again — by someone who says so, not by this resume.
                return False
        return True

    def _block(self, task: TaskRecord, question: str) -> None:
        self.store.set_state(task.id, TaskState.BLOCKED, error=question)
        self.bus.emit(Topic.TASK_BLOCKED, task_id=task.id, question=question)

    # -------------------------------------------------------------------- read

    def get(self, task_id: str) -> TaskRecord | None:
        return self.store.get(task_id)

    def list(self, **kw: Any) -> Sequence[TaskRecord]:
        return self.store.list(**kw)

    def steps(self, task_id: str) -> Sequence[StepRecord]:
        return self.store.steps_for(task_id)

    async def wait(self, task_id: str, *, timeout: float | None = None) -> TaskRecord:
        """Block until a task leaves PENDING/RUNNING. Used by the CLI and tests."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            task = self.store.require(task_id)
            if not task.state.active or task.state is TaskState.BLOCKED:
                return task
            if deadline is not None and time.monotonic() >= deadline:
                return task
            await asyncio.sleep(0.05)

    async def wait_all(self, *, timeout: float | None = None) -> None:
        pending = [t for t in self._running.values() if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    def active_count(self) -> int:
        return len(self.store.list(active_only=True))


__all__ = ["TaskSupervisor"]
