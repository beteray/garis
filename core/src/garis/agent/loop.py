"""The execution loop: plan → act → verify → repair → report.

Where "GARIS handles its own errors" lives. A failing step is not news for the
user; it is input to a repair strategy:

    1. retry           — transient things (busy file, flaky network, timeout)
    2. fix the step    — bad parameters or a wrong tool choice: replan from here
    3. change approach — ask the planner for a different route entirely
    4. skip            — the step was optional
    5. report          — only now, and only in plain language

Resumability is built on the journal, not on memory: completed step keys are read
back before execution, so a task interrupted by a reboot continues rather than
restarting.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..config import Config
from ..errors import (
    ApprovalDenied,
    ApprovalRequired,
    GarisError,
    NoModelAvailable,
    PolicyDenied,
    TaskAborted,
)
from ..events import EventBus, Topic
from ..runtime import Action, ActionResult, Runtime
from .goal import Goal, Plan, PlanStep
from .planner import Planner
from .report import Report, blocked_report, build_report, failed_report
from .verify import StepEvidence, Verification, Verifier

MAX_REPLANS = 2
RETRY_BACKOFF = (1.0, 3.0, 8.0)


class AgentState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class Journal(Protocol):
    """Durable step record. ``TaskStore`` implements this."""

    def save_plan(self, task_id: str, plan: Plan) -> None: ...

    def completed_steps(self, task_id: str) -> dict[str, Any]: ...

    def step_started(self, task_id: str, step: PlanStep, ordinal: int) -> None: ...

    def step_finished(self, task_id: str, step: PlanStep, result: ActionResult) -> None: ...


@dataclass(slots=True)
class Outcome:
    """Result of one whole run of the loop."""

    state: AgentState
    report: Report
    plan: Plan
    evidence: list[StepEvidence] = field(default_factory=list)
    verification: Verification | None = None
    approval_id: str = ""
    question: str = ""
    resumable: bool = False

    @property
    def ok(self) -> bool:
        return self.state is AgentState.DONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "report": self.report.to_dict(),
            "question": self.question,
            "approval_id": self.approval_id,
            "resumable": self.resumable,
            "verification": self.verification.to_dict() if self.verification else None,
        }


class AgentLoop:
    def __init__(
        self,
        runtime: Runtime,
        planner: Planner,
        verifier: Verifier,
        *,
        bus: EventBus,
        config: Config,
        journal: Journal | None = None,
    ) -> None:
        self.runtime = runtime
        self.planner = planner
        self.verifier = verifier
        self.bus = bus
        self.config = config
        self.journal = journal

    # --------------------------------------------------------------------- run

    async def run(self, goal: Goal, *, task_id: str) -> Outcome:
        started = time.monotonic()
        self._state(task_id, AgentState.THINKING)

        try:
            plan = await self.planner.make_plan(goal)
        except NoModelAvailable as exc:
            # Not a failure of the task — a missing prerequisite the user can
            # supply. Blocked says "waiting for you" and keeps the goal, which is
            # what a person needs to see; failed says "I tried and could not",
            # which would be a claim about work that never started.
            self._state(task_id, AgentState.BLOCKED)
            question = exc.explain()
            return Outcome(
                AgentState.BLOCKED,
                blocked_report(goal, question),
                Plan(),
                question=question,
                resumable=True,
            )
        except GarisError as exc:
            return Outcome(
                AgentState.FAILED,
                failed_report(goal, "Nie udało mi się zaplanować tego zadania.",
                              details=str(exc)),
                Plan(),
            )

        if plan.blocked:
            self._state(task_id, AgentState.BLOCKED)
            return Outcome(
                AgentState.BLOCKED,
                blocked_report(goal, plan.question),
                plan,
                question=plan.question,
                resumable=True,
            )

        if self.journal is not None:
            self.journal.save_plan(task_id, plan)

        evidence: list[StepEvidence] = []
        failures: list[str] = []
        completed = self.journal.completed_steps(task_id) if self.journal else {}
        replans = 0

        self._state(task_id, AgentState.WORKING)
        index = 0
        while index < len(plan.steps):
            step = plan.steps[index]

            if step.key in completed:
                # Already done in an earlier life of this task.
                evidence.append(
                    StepEvidence(
                        target=step.target,
                        purpose=step.purpose,
                        ok=True,
                        expects=step.expects,
                        summary=_summarise(completed[step.key]),
                        value=completed[step.key],
                        extras={"resumed": True},
                    )
                )
                index += 1
                continue

            try:
                result = await self._run_step(goal, step, index, task_id)
            except ApprovalRequired as exc:
                # Park the task. Persisted approval + journal means the yes can
                # arrive tomorrow, from a phone, after a reboot.
                self._state(task_id, AgentState.BLOCKED)
                return Outcome(
                    AgentState.BLOCKED,
                    blocked_report(goal, exc.prompt),
                    plan,
                    evidence=evidence,
                    approval_id=exc.request_id,
                    question=exc.prompt,
                    resumable=True,
                )
            except ApprovalDenied as exc:
                return Outcome(
                    AgentState.FAILED,
                    failed_report(goal, exc.explain()),
                    plan,
                    evidence=evidence,
                )
            except PolicyDenied as exc:
                return Outcome(
                    AgentState.FAILED,
                    failed_report(goal, exc.explain(), details=str(exc)),
                    plan,
                    evidence=evidence,
                )
            except TaskAborted:
                raise

            if result.ok:
                evidence.append(
                    StepEvidence(
                        target=step.target,
                        purpose=step.purpose,
                        ok=True,
                        expects=step.expects,
                        summary=_summarise(result.value),
                        # The tool's own return value, kept structured: the
                        # verifier checks numbers, and a flattened string cannot
                        # be checked at all.
                        value=result.value,
                        # A capability that checked its own postcondition against
                        # the machine already answered the question the task-level
                        # verifier is about to ask. Dropping that answer here is
                        # how a measured result reaches the report as "nie
                        # sprawdzałem".
                        verified=result.verified,
                        note=result.detail,
                        extras={"duration_ms": result.duration_ms},
                    )
                )
                index += 1
                continue

            failures.append(f"{step.name}: {result.error}")

            if step.optional:
                evidence.append(
                    StepEvidence(step.target, step.purpose, ok=False, skipped=True,
                                 error=result.error or "", expects=step.expects)
                )
                index += 1
                continue

            if replans < MAX_REPLANS:
                replans += 1
                self._state(task_id, AgentState.THINKING)
                self.bus.emit(
                    Topic.TASK_PROGRESS,
                    task_id=task_id,
                    message="Pierwsza metoda nie zadziałała, próbuję inaczej.",
                    silent=True,
                )
                try:
                    fresh = await self.planner.repair_plan(
                        goal,
                        failures=failures,
                        done=[f"{e.name}: {e.purpose}" for e in evidence if e.ok],
                    )
                except GarisError:
                    fresh = Plan()
                if fresh.steps:
                    # Replace what is left, keep what already ran.
                    plan = Plan(
                        summary=plan.summary or fresh.summary,
                        steps=plan.steps[:index] + fresh.steps,
                        assumptions=plan.assumptions or fresh.assumptions,
                        notes=fresh.notes,
                    ).rekey()
                    if self.journal is not None:
                        self.journal.save_plan(task_id, plan)
                    self._state(task_id, AgentState.WORKING)
                    continue

            evidence.append(
                StepEvidence(step.target, step.purpose, ok=False,
                             error=result.error or "nieznany błąd", expects=step.expects)
            )
            break

        self._state(task_id, AgentState.VERIFYING)
        verification = await self.verifier.check(goal, evidence, plan=plan)

        # Fail closed. "Done" requires that something actually ran and returned
        # successfully — not that the plan finished, not that no error was
        # raised, and certainly not that a verifier said so. A plan with no
        # executable steps used to reach this line and be reported as done.
        executed = [e for e in evidence if e.ok and not e.skipped]
        state = AgentState.DONE if (executed and verification.ok) else AgentState.FAILED
        if not executed and verification.ok:
            verification = Verification(
                goal_met=False,
                reason="Nie wykonałem żadnego kroku, więc nie ma czego uznać za zrobione.",
                checked=True,
                checked_by="rules",
            )

        report = build_report(
            goal, plan, evidence, verification,
            duration_seconds=time.monotonic() - started, repairs=replans,
        )
        self._state(task_id, state)
        return Outcome(state, report, plan, evidence=evidence, verification=verification)

    # -------------------------------------------------------------------- steps

    async def _run_step(
        self, goal: Goal, step: PlanStep, ordinal: int, task_id: str
    ) -> ActionResult:
        """Execute one step, retrying transient failures in place."""
        if self.journal is not None:
            self.journal.step_started(task_id, step, ordinal)

        attempts = max(1, self.config.autonomy.max_repair_attempts)
        result: ActionResult | None = None

        for attempt in range(attempts):
            action = Action(
                # `Action.tool` carries the identity of both kinds of step —
                # capabilities have been writing their id here since commit 3,
                # and `fingerprint()` (which matches a stored approval to the
                # retry after a reboot) is built from it. Splitting that now
                # would invalidate approvals the user has already answered.
                tool=step.name,
                params=dict(step.params),
                intent=step.purpose or goal.text,
                task_id=task_id,
                step_key=step.key,
                target=goal.target,
            )
            self.bus.emit(
                Topic.TASK_STEP,
                task_id=task_id,
                step=step.key,
                tool=step.name,
                target=step.name,
                purpose=step.purpose,
                attempt=attempt + 1,
            )
            result = await self.runtime.perform_step(step.target, action)
            if result.ok:
                break
            if not result.retryable:
                break
            if attempt + 1 < attempts:
                await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

        assert result is not None
        if self.journal is not None:
            self.journal.step_finished(task_id, step, result)
        return result

    def _state(self, task_id: str, state: AgentState) -> None:
        self.bus.emit(Topic.AGENT_STATE, task_id=task_id, state=state.value)


def _summarise(value: Any, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = "; ".join(f"{k}: {_short(v)}" for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        head = "; ".join(_short(v) for v in value[:8])
        text = f"{len(value)} pozycji: {head}"
    else:
        text = str(value)
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _short(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["MAX_REPLANS", "AgentLoop", "AgentState", "Journal", "Outcome"]
