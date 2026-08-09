"""The one execution envelope. Everything GARIS does passes through here once.

Before this file there were two ways to make something happen. ``Runtime.perform``
had policy, approvals, leases and an audit trail, but no effect identity, no
evidence and no verifier. ``CapabilityRegistry.perform`` had typed evidence and a
mandatory verifier, but no policy, no audit and an idempotence dict that a
restart erased. Neither was wrong on its own; having both meant every guarantee
was true of half the system, and which half depended on how the work happened to
be spelled.

So there is one envelope now, and the executors are the only thing that differs:

    Runtime.perform → compatibility adapter → CapabilityRunner
                                                 ├→ LegacyToolExecutor  → tool handler
                                                 └→ NativeCapabilityExecutor → capability

An executor runs the work and returns what happened. It does not evaluate
policy, ask for approval, reserve an effect, write an audit row, persist
anything or publish an event — those happen exactly once each, here, in one
order, for both kinds of target.

The order is fixed and the reasons are not stylistic:

    1  resolve the target            9  refuse replay of an uncertain effect
    2  validate arguments           10  execute, through exactly one executor
    3  check cancellation           11  collect structured output
    4  evaluate policy              12  construct evidence
    5  request approval             13  run independent verification
    6  derive effect_id + args hash 14  persist settlement + evidence + event
    6b acquire leases               15  dispatch what committed
    7  reserve the effect           16  return a structured result
    8  replay a successful effect

Policy and approval sit ahead of the reservation because a denial must leave no
trace in the world. Persistence sits ahead of the event because an announcement
about something that did not commit is the same family of untruth as a stub
reporting "sprawdzone". And nothing is marked successful until its evidence and
verdict have committed with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import (
    ApprovalDenied,
    ApprovalRequired,
    GarisError,
    LeaseTimeout,
    PolicyDenied,
    StoreError,
)
from ..events import Topic
from ..kernel.contracts import (
    MAX_NESTING_DEPTH,
    CapabilityError,
    CapabilityTarget,
    Effect,
    EffectDisposition,
    EvidenceRecord,
    ExecutionContext,
    Failure,
    PolicyEstimate,
    PolicySubject,
    RuntimeProfile,
    StepTarget,
    Verification,
)
from ..kernel.contracts import (
    redact as redact_evidence,
)
from ..kernel.effects import EffectState, EffectStore, arguments_hash
from ..kernel.outbox import EventOutbox
from .action import Action
from .approvals import ApprovalBroker
from .audit import AuditLog
from .audit import redact as redact_params
from .leases import LeaseManager, LeaseMode
from .policy import Decision, PolicyEngine, Verdict

# Effects that mean "no one else may touch this at the same time".
_EXCLUSIVE = frozenset(
    {
        Effect.WRITE,
        Effect.DELETE_PERMANENT,
        Effect.INSTALL,
        Effect.SYSTEM_CONFIG,
        Effect.CREDENTIALS,
        Effect.INPUT_CONTROL,
        Effect.EXEC,
    }
)

#: The audit outcomes a single invocation can end in. Exactly one row is written
#: per invocation and it carries exactly one of these, so "what happened" is a
#: value to group by rather than a sentence to grep.
AUDIT_POLICY_DENIED = "denied"
AUDIT_APPROVAL_DENIED = "rejected_by_user"
AUDIT_AWAITING_APPROVAL = "awaiting_approval"
AUDIT_REPLAYED = "replayed"
AUDIT_OK = "ok"
AUDIT_POSTCONDITION_FAILED = "postcondition_failed"
AUDIT_UNCERTAIN = "uncertain"
AUDIT_CANCELLED = "cancelled"
AUDIT_PERSISTENCE_FAILED = "persistence_failed"
AUDIT_ERROR = "error"


# ------------------------------------------------------------------ executors


@dataclass(frozen=True, slots=True)
class Resolved:
    """What the executor found behind a target, in the runner's vocabulary."""

    subject: PolicySubject
    version: int = 1
    estimate: PolicyEstimate = field(default_factory=PolicyEstimate)
    #: Lease keys this work needs. Derived from the target's own declaration,
    #: never from the caller's description of it.
    resource_keys: tuple[str, ...] = ()
    timeout: float = 120.0
    #: Whatever the executor needs to hand back to itself at step 10. Opaque to
    #: the runner on purpose: the runner must not be able to act on it.
    handle: Any = None


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    """What an executor observed. Never prose, never a verdict."""

    ok: bool
    value: Any = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    error_kind: str = ""
    retryable: bool = True
    #: The executor got far enough that it cannot say whether the world moved.
    uncertain: bool = False
    failure: Failure | None = None
    #: What happened outside GARIS, when the executor is in a position to say.
    #: `None` means it did not, and the runner supplies the safe reading.
    disposition: EffectDisposition | None = None


class TargetExecutor(Protocol):
    """The whole contract an executor has to meet. Deliberately four methods.

    Anything larger is an invitation to put a second policy check, a second
    audit row or a second idempotence mechanism inside an executor, which is the
    shape this file exists to remove.
    """

    def resolve(self, target: StepTarget, action: Action) -> Resolved:
        """Find the target and describe it. Raises for unknown or unsupported."""

    def validate(self, resolved: Resolved, params: Mapping[str, Any]) -> dict[str, Any]:
        """Coerce and check arguments, or raise."""

    async def execute(
        self,
        resolved: Resolved,
        params: Mapping[str, Any],
        context: ExecutionContext,
        action: Action,
    ) -> ExecutionOutput:
        """Do the work. Nothing else."""

    async def verify(
        self, resolved: Resolved, params: Mapping[str, Any], output: ExecutionOutput
    ) -> Verification:
        """Check the postcondition, independently of what the work reported."""

    def goal(
        self, resolved: Resolved, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """What this call is trying to achieve, for the effect record.

        Asked after validation and before anything is reserved, because the
        reservation is the last moment guaranteed to happen. Saying nothing is a
        legitimate answer and the default: a target that cannot express its goal
        as data should not invent one, and recovery will report `MANUAL_REQUIRED`
        rather than compare the world against a guess.
        """
        return {}


# --------------------------------------------------------------------- result


@dataclass(frozen=True, slots=True)
class RunnerResult:
    """One structured answer, whatever ran and however it ended.

    Every claim is a separate field because collapsing them is the original
    defect: `ok` is not `verified`, `verified` is not "nobody checked", and
    "it failed" is not "it may have happened".
    """

    target: str
    source: str
    effect_id: str
    ok: bool = False
    value: Any = None
    #: Redacted. Safe to hand to the window; the full record stays in the effect.
    evidence: tuple[Mapping[str, Any], ...] = ()
    verification: Verification = field(
        default_factory=lambda: Verification(goal_met=False)
    )
    failure: Failure | None = None
    uncertain: bool = False
    replayed: bool = False
    disposition: EffectDisposition = EffectDisposition.NOT_STARTED
    attempt_number: int = 1
    audit_id: int = 0
    event_id: str = ""
    error: str = ""
    error_kind: str = ""
    retryable: bool = True
    duration_ms: int = 0
    #: Set when policy or the user refused. The façade turns these into the
    #: typed exceptions that callers have always caught.
    denial: Verdict | None = None
    approval_request_id: str = ""
    approval_prompt: str = ""

    @property
    def verified(self) -> bool:
        return self.verification.verified


# --------------------------------------------------------------------- runner


class CapabilityRunner:
    def __init__(
        self,
        *,
        policy: PolicyEngine,
        approvals: ApprovalBroker,
        audit: AuditLog,
        effects: EffectStore,
        outbox: EventOutbox,
        leases: LeaseManager | None = None,
        profile: RuntimeProfile = RuntimeProfile.PRODUCTION,
    ) -> None:
        self.policy = policy
        self.approvals = approvals
        self.audit = audit
        self.effects = effects
        self.outbox = outbox
        self.leases = leases or LeaseManager()
        self.profile = profile
        self._executors: dict[str, TargetExecutor] = {}

    def register_executor(self, source: str, executor: TargetExecutor) -> None:
        """Bind one executor to one kind of target.

        Registration rather than an import so this layer never has to know that
        the capability package exists — it sits above ``runtime`` and importing
        it here would close a cycle.
        """
        self._executors[source] = executor

    def executor_for(self, target: StepTarget) -> TargetExecutor:
        source = "capability" if isinstance(target, CapabilityTarget) else "legacy_tool"
        try:
            return self._executors[source]
        except KeyError:
            raise CapabilityError(
                Failure.UNSUPPORTED_PLATFORM,
                f"nie podłączono wykonawcy dla celu typu {source!r}",
                user_message="Nie mam czym tego wykonać.",
            ) from None

    # ------------------------------------------------------------------- run

    async def run(
        self,
        target: StepTarget,
        action: Action,
        context: ExecutionContext | None = None,
    ) -> RunnerResult:
        """The sixteen steps, in order, for every target GARIS has."""
        started = time.monotonic()
        source = "capability" if isinstance(target, CapabilityTarget) else "legacy_tool"
        context = context or ExecutionContext(
            task_id=action.task_id or "", step_key=action.step_key or "",
            runtime_profile=self.profile,
        )

        if context.depth > MAX_NESTING_DEPTH:
            return self._settle_gate(
                target.name, source, action, None,
                Failure.INVALID_INPUT, AUDIT_ERROR,
                f"przekroczono dopuszczalne zagnieżdżenie ({MAX_NESTING_DEPTH})",
                started, error_kind="invalid", retryable=False, context=context,
            )

        executor = self.executor_for(target)

        # 1. resolve ---------------------------------------------------------
        try:
            resolved = executor.resolve(target, action)
        except GarisError as exc:
            kind = "unsupported" if type(exc).__name__ == "Unsupported" else "not_found"
            failure = (
                Failure.UNSUPPORTED_PLATFORM if kind == "unsupported"
                else Failure.INVALID_INPUT
            )
            return self._settle_gate(
                target.name, source, action, None, failure, AUDIT_ERROR, str(exc),
                started, error_kind=kind, retryable=False, context=context,
            )

        # 2. validate --------------------------------------------------------
        try:
            params = executor.validate(resolved, action.params)
        except GarisError as exc:
            kind = "unsupported" if type(exc).__name__ == "Unsupported" else "invalid"
            return self._settle_gate(
                target.name, source, action, resolved,
                Failure.UNSUPPORTED_PLATFORM if kind == "unsupported"
                else Failure.INVALID_INPUT,
                AUDIT_ERROR, str(exc), started,
                error_kind=kind, retryable=kind == "invalid", context=context,
            )
        except ValueError as exc:
            return self._settle_gate(
                target.name, source, action, resolved, Failure.INVALID_INPUT,
                AUDIT_ERROR, str(exc), started, error_kind="invalid",
                retryable=True, context=context,
            )
        # The runtime works on validated arguments from here on, including the
        # ones the effect id is derived from: a plan that omits an optional
        # parameter and one that passes its default are the same act.
        action.params = dict(params)

        # 3. cancellation ----------------------------------------------------
        if context.cancelled():
            return self._settle_gate(
                target.name, source, action, resolved, Failure.CANCELLED,
                AUDIT_CANCELLED, "przerwane przed wykonaniem", started,
                error_kind="cancelled", retryable=True, context=context,
            )

        # 4. policy ----------------------------------------------------------
        verdict = self.policy.evaluate_subject(resolved.subject, action, resolved.estimate)
        if verdict.decision is Decision.DENY:
            return self._settle_gate(
                target.name, source, action, resolved, Failure.PERMISSION_DENIED,
                AUDIT_POLICY_DENIED, verdict.reason, started, verdict=verdict,
                topic=Topic.ACTION_DENIED, error_kind="denied", retryable=False,
                context=context, denial=verdict,
            )

        # 5. approval --------------------------------------------------------
        if verdict.decision is Decision.CONFIRM:
            gate = self._approval(target.name, source, action, resolved, verdict,
                                  started, context)
            if gate is not None:
                return gate

        # 6. identity --------------------------------------------------------
        effect_id = context.effect_id or derive_effect_id(
            target.name, action, resolved.subject
        )
        fingerprint = arguments_hash(params)

        # 6b. leases ---------------------------------------------------------
        mode = (
            LeaseMode.EXCLUSIVE if (resolved.subject.effects & _EXCLUSIVE)
            else LeaseMode.SHARED
        )
        holder = action.task_id or f"action:{action.id}"
        try:
            lease = await self.leases.acquire(
                resolved.resource_keys, holder=holder, mode=mode,
                timeout=resolved.timeout,
            )
        except LeaseTimeout as exc:
            return self._settle_gate(
                target.name, source, action, resolved, Failure.PROVIDER_UNAVAILABLE,
                AUDIT_ERROR, str(exc), started, verdict=verdict, error_kind="busy",
                retryable=True, context=context,
            )

        try:
            return await self._reserve_and_run(
                target, source, action, resolved, verdict, executor, params,
                effect_id, fingerprint, context, started,
            )
        finally:
            lease.release()

    # -------------------------------------------------------- 7 through 16

    async def _reserve_and_run(
        self,
        target: StepTarget,
        source: str,
        action: Action,
        resolved: Resolved,
        verdict: Verdict,
        executor: TargetExecutor,
        params: dict[str, Any],
        effect_id: str,
        fingerprint: str,
        context: ExecutionContext,
        started: float,
    ) -> RunnerResult:
        # 7. reserve, and announce the start in the same transaction ---------
        try:
            with self.effects.db.transaction() as conn:
                reservation = self.effects.reserve_in(
                    conn, effect_id, capability_id=target.name,
                    task_id=action.task_id or "", step_key=action.step_key or "",
                    args=params, goal=_goal_of(executor, resolved, params),
                )
                if reservation.granted:
                    self.outbox.stage(
                        conn, Topic.ACTION_STARTED, task_id=action.task_id or "",
                        payload={
                            "tool": action.tool,
                            "target": target.name,
                            "source": source,
                            "intent": action.intent,
                            "effect_id": effect_id,
                            "parent_effect_id": context.parent_effect_id,
                            "effects": sorted(
                                e.value for e in resolved.subject.effects
                            ),
                        },
                    )
        except CapabilityError as exc:
            # 8b. the same id for different arguments. Never guess which act won.
            return self._settle_gate(
                target.name, source, action, resolved, exc.failure, AUDIT_ERROR,
                exc.detail or str(exc), started, verdict=verdict,
                error_kind="invalid", retryable=False, context=context,
                effect_id=effect_id,
            )
        except StoreError as exc:
            return self._settle_gate(
                target.name, source, action, resolved, Failure.PERSISTENCE_FAILED,
                AUDIT_PERSISTENCE_FAILED, str(exc), started, verdict=verdict,
                error_kind="persistence", retryable=True, context=context,
                effect_id=effect_id,
            )

        if not reservation.granted:
            return self._replay(
                target.name, source, action, resolved, verdict, reservation.record,
                started, context,
            )

        self.outbox.drain()  # 15, for the start: published only now it committed

        # 10. execute --------------------------------------------------------
        # Whether the adapter was actually reached is recorded as a fact, not
        # deduced afterwards: it decides NOT_STARTED versus UNKNOWN, and so
        # decides whether this may ever be run again.
        crossed: list[bool] = []
        output = await self._execute(
            executor, resolved, params, context, action, crossed
        )
        disposition = decide_disposition(
            executor_started=bool(crossed),
            effectful=resolved.subject.effectful,
            output=output,
        )

        # 13. verify, independently of what the work said about itself -------
        if output.ok and not output.uncertain:
            try:
                verification = await executor.verify(resolved, params, output)
            except Exception as exc:  # a broken verifier proves nothing
                verification = Verification.unchecked(
                    f"Sprawdzenie po fakcie nie powiodło się: {type(exc).__name__}: {exc}"
                )
        elif output.uncertain:
            verification = Verification.uncertain_effect(
                output.error or "Nie wiem, czy operacja doszła do skutku."
            )
        else:
            verification = Verification(
                goal_met=False, checked=True, checked_by="rules",
                reason=output.error or "Nie powiodło się.",
            )

        # 12. evidence -------------------------------------------------------
        records = (
            (EvidenceRecord.new(target.name, resolved.version, output.evidence),)
            if output.evidence else ()
        )
        verification = _attach_evidence(verification, records)

        # 14 + 15. persist, then publish what committed ----------------------
        return self._settle(
            target, source, action, resolved, verdict, output, verification,
            records, effect_id, started, context, disposition,
            reservation.record.attempt_number,
        )

    async def _execute(
        self,
        executor: TargetExecutor,
        resolved: Resolved,
        params: dict[str, Any],
        context: ExecutionContext,
        action: Action,
        crossed: list[bool],
    ) -> ExecutionOutput:
        """Step 10 and 11. The only place work happens, and the only place a
        crash is turned into a fact rather than allowed to escape.

        ``crossed`` records whether the call boundary was actually reached. It
        is set immediately before the adapter is invoked and read afterwards,
        because "did execution start?" is the difference between `NOT_STARTED`
        and `UNKNOWN` — between an effect that certainly did not happen and one
        that might have. Inferring it from the exception type, the elapsed time
        or the absence of output would be guessing about the world.
        """
        try:
            crossed.append(True)
            return await executor.execute(resolved, params, context, action)
        except asyncio.CancelledError:
            raise
        except (ApprovalRequired, PolicyDenied, ApprovalDenied):
            raise  # a nested action needs the user; let it reach the task
        except CapabilityError as exc:
            return ExecutionOutput(
                ok=False, error=exc.detail or str(exc), error_kind="error",
                uncertain=exc.uncertain, failure=exc.failure,
            )
        except Exception as exc:
            # It got far enough to fail, and an exception says nothing about
            # whether the world moved first: a message sent and then lost on the
            # readback raises exactly like one never sent. For effectful work the
            # only honest answer is that nobody knows.
            return ExecutionOutput(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                error_kind="crash",
                uncertain=resolved.subject.effectful,
                failure=Failure.EXECUTOR_FAILED,
            )

    # ----------------------------------------------------------- settlement

    def _settle(
        self,
        target: StepTarget,
        source: str,
        action: Action,
        resolved: Resolved,
        verdict: Verdict,
        output: ExecutionOutput,
        verification: Verification,
        records: tuple[EvidenceRecord, ...],
        effect_id: str,
        started: float,
        context: ExecutionContext,
        disposition: EffectDisposition,
        attempt: int,
    ) -> RunnerResult:
        """Step 14: one transaction for the settlement, the audit row and the
        event. Nothing is successful until all three commit."""
        duration = _ms(started)
        safe = tuple(redact_evidence(r.fields) for r in records)

        uncertain = output.uncertain
        # "It ran and returned" and "the goal was met" are different statements,
        # and this is the seam where collapsing them would start. A postcondition
        # that came back negative leaves `ok` true — the executor did work, and
        # pretending otherwise would strand a caller that needs the value — while
        # `verified` stays false and the failure type says exactly what is wrong.
        if output.ok and not verification.goal_met and verification.checked:
            failure: Failure | None = Failure.POSTCONDITION_FAILED
            audit_outcome = AUDIT_POSTCONDITION_FAILED
            topic = Topic.ACTION_FINISHED
        elif uncertain:
            failure = Failure.EFFECT_UNCERTAIN
            audit_outcome = AUDIT_UNCERTAIN
            topic = Topic.ACTION_FAILED
        elif output.ok:
            failure = None
            audit_outcome = AUDIT_OK
            topic = Topic.ACTION_FINISHED
        else:
            failure = output.failure or Failure.EXECUTOR_FAILED
            audit_outcome = AUDIT_ERROR
            topic = Topic.ACTION_FAILED

        detail = output.error or _summarise(output.value)
        # An effect is repeatable only when the executor never ran or proved it
        # changed nothing. Everything else — success, polite failure, crash —
        # has either happened or might have, and running it again is how one
        # install becomes two.
        retryable = output.retryable and disposition.permits_retry
        payload = {
            "tool": action.tool,
            "target": target.name,
            "source": source,
            "effect_id": effect_id,
            "attempt_number": attempt,
            "parent_effect_id": context.parent_effect_id,
            "duration_ms": duration,
            "summary": _summarise(output.value),
            "disposition": disposition.value,
            "retryable": retryable,
            "goal_met": verification.goal_met,
            "checked": verification.checked,
            "verified": verification.verified,
            "uncertain": verification.uncertain or uncertain,
            "failure_type": failure.value if failure else "",
        }
        if failure is not None:
            payload["error"] = output.error
            payload["kind"] = output.error_kind or "error"

        try:
            with self.effects.db.transaction() as conn:
                self.effects.settle_in(
                    conn, effect_id,
                    # The effect is settled on what the executor did, not on
                    # what the verifier concluded: work that ran and failed its
                    # postcondition still happened, and must never be repeated
                    # as if it had not.
                    ok=output.ok,
                    outcome={"value": output.value},
                    evidence=records,
                    reason=output.error,
                    uncertain=uncertain,
                    disposition=disposition,
                    # The whole verdict, so a replay hands back what was measured
                    # instead of what the effect's state implies.
                    verification=verification.to_dict(),
                )
                audit_id = self.audit.stage(
                    conn,
                    task_id=action.task_id,
                    tool=action.tool,
                    intent=action.intent,
                    effects=sorted(e.value for e in resolved.subject.effects),
                    decision=verdict.decision.value,
                    rule=verdict.rule,
                    params=action.params,
                    outcome=audit_outcome,
                    detail=f"[{disposition.value}] {detail}" if detail
                    else f"[{disposition.value}]",
                    duration_ms=duration,
                )
                event = self.outbox.stage(
                    conn, topic, task_id=action.task_id or "", payload=payload
                )
        except StoreError as exc:
            # The work ran and we cannot write down what it did. Marking it
            # successful would be a claim resting on nothing; the reservation
            # stays unsettled and the startup sweep will call it uncertain,
            # which is exactly what it is.
            return RunnerResult(
                target=target.name, source=source, effect_id=effect_id, ok=False,
                failure=Failure.PERSISTENCE_FAILED, uncertain=True,
                verification=Verification.uncertain_effect(
                    "Operacja się wykonała, ale nie udało się jej zapisać."
                ),
                error=str(exc), error_kind="persistence", retryable=False,
                duration_ms=duration,
            )

        self.outbox.drain()  # 15

        return RunnerResult(
            target=target.name,
            source=source,
            effect_id=effect_id,
            ok=output.ok,
            value=output.value,
            evidence=safe,
            verification=verification,
            failure=failure,
            uncertain=uncertain,
            audit_id=audit_id,
            event_id=event.event_id,
            error=output.error,
            error_kind=output.error_kind,
            retryable=retryable,
            disposition=disposition,
            attempt_number=attempt,
            duration_ms=duration,
        )

    def _replay(
        self,
        name: str,
        source: str,
        action: Action,
        resolved: Resolved,
        verdict: Verdict,
        record: Any,
        started: float,
        context: ExecutionContext,
    ) -> RunnerResult:
        """Steps 8 and 9. What happened is handed back; nothing is done again.

        Refusing to replay an uncertain effect is the point of having the state
        at all: retrying something that might have worked is how one payment
        becomes two.

        The stored verdict is returned **exactly as it was recorded**. Rebuilding
        it from the effect's state would answer the wrong question — `DONE` means
        the record was settled truthfully, not that the user got what they asked
        for. A volume set to 33% when 30% was requested is `DONE`, `APPLIED` and
        `goal_met=False` all at once, and a replay that inferred `goal_met=True`
        from `DONE` would turn a measured failure into a success on the way back
        out.
        """
        replaying = record.state is EffectState.DONE
        outcome = dict(record.outcome or {})
        uncertain = record.state is EffectState.UNCERTAIN

        if replaying:
            stored = record.verification
            if stored:
                verification = Verification.from_dict(stored)
            else:
                # Settled by an older build that kept no verdict. Nobody checked
                # anything we can point at, so nothing is claimed.
                verification = Verification.unchecked(
                    "Ten efekt już się wydarzył; nie mam zapisanego sprawdzenia."
                )
            failure: Failure | None = (
                None if verification.goal_met or not verification.checked
                else Failure.POSTCONDITION_FAILED
            )
            audit_outcome = AUDIT_REPLAYED
            error = "" if failure is None else verification.reason
        elif uncertain:
            verification = Verification.uncertain_effect(record.reason or (
                "Nie wiem, czy ta operacja doszła do skutku — nie powtarzam jej."
            ))
            failure = Failure.EFFECT_UNCERTAIN
            audit_outcome = AUDIT_UNCERTAIN
            error = record.reason or "Efekt niepewny — nie powtarzam automatycznie."
        else:
            # RESERVED: somebody else holds it right now.
            verification = Verification.uncertain_effect(
                "Ta operacja jest właśnie wykonywana gdzie indziej."
            )
            failure = Failure.EFFECT_UNCERTAIN
            audit_outcome = AUDIT_UNCERTAIN
            error = "Ten efekt jest już w toku."

        duration = _ms(started)
        with self.effects.db.transaction() as conn:
            audit_id = self.audit.stage(
                conn,
                task_id=action.task_id,
                tool=action.tool,
                intent=action.intent,
                effects=sorted(e.value for e in resolved.subject.effects),
                decision=verdict.decision.value,
                rule=verdict.rule,
                params=action.params,
                outcome=audit_outcome,
                detail=f"[{record.disposition.value}] " + (error or "odtworzony efekt"),
                duration_ms=duration,
            )
            event = self.outbox.stage(
                conn,
                Topic.ACTION_FINISHED if replaying else Topic.ACTION_FAILED,
                task_id=action.task_id or "",
                payload={
                    "tool": action.tool,
                    "target": name,
                    "source": source,
                    "effect_id": record.effect_id,
                    "attempt_number": record.attempt_number,
                    "replayed": replaying,
                    "uncertain": uncertain,
                    "disposition": record.disposition.value,
                    "retryable": False,
                    "goal_met": verification.goal_met,
                    "checked": verification.checked,
                    "duration_ms": duration,
                    "failure_type": failure.value if failure else "",
                },
            )
        self.outbox.drain()

        return RunnerResult(
            target=name, source=source, effect_id=record.effect_id, ok=replaying,
            value=outcome.get("value"),
            evidence=tuple(redact_evidence(e.get("fields", {})) for e in record.evidence),
            verification=verification, failure=failure, uncertain=uncertain,
            replayed=replaying, audit_id=audit_id, event_id=event.event_id,
            error=error, error_kind="uncertain" if uncertain else (
                "postcondition" if failure else ""
            ),
            # Never. A replayed effect already happened, and one that is
            # uncertain or in flight must not be attempted on a hunch.
            retryable=False,
            disposition=record.disposition,
            attempt_number=record.attempt_number,
            duration_ms=duration,
        )

    # ---------------------------------------------------------------- gates

    def _approval(
        self,
        name: str,
        source: str,
        action: Action,
        resolved: Resolved,
        verdict: Verdict,
        started: float,
        context: ExecutionContext,
    ) -> RunnerResult | None:
        """Step 5. Returns a result when the flow must stop, None to carry on."""
        fingerprint = action.fingerprint()
        effects = tuple(sorted(e.value for e in resolved.subject.effects))

        denied = self.approvals.denied_for(fingerprint)
        if denied is not None:
            self.approvals.consume(denied.id)
            return self._settle_gate(
                name, source, action, resolved, Failure.APPROVAL_DENIED,
                AUDIT_APPROVAL_DENIED, "użytkownik odrzucił operację", started,
                verdict=verdict, topic=Topic.ACTION_DENIED, error_kind="denied",
                retryable=False, context=context,
            )

        granted = self.approvals.granted_for(fingerprint)
        if granted is not None:
            # One yes authorises exactly one execution of exactly this operation.
            self.approvals.consume(granted.id)
            return None

        request = self.approvals.request_for(
            action, verdict, effects=effects,
            redacted_params=redact_params(action.params),
        )
        result = self._settle_gate(
            name, source, action, resolved, Failure.APPROVAL_DENIED,
            AUDIT_AWAITING_APPROVAL, request.prompt, started, verdict=verdict,
            topic=Topic.ACTION_DENIED, error_kind="needs_approval",
            retryable=True, context=context, publish=False,
        )
        return RunnerResult(
            **{
                **{f.name: getattr(result, f.name) for f in _RESULT_FIELDS},
                "approval_request_id": request.id,
                "approval_prompt": request.prompt,
            }
        )

    def _settle_gate(
        self,
        name: str,
        source: str,
        action: Action,
        resolved: Resolved | None,
        failure: Failure,
        audit_outcome: str,
        detail: str,
        started: float,
        *,
        verdict: Verdict | None = None,
        topic: str = Topic.ACTION_FAILED,
        error_kind: str = "error",
        retryable: bool = True,
        context: ExecutionContext | None = None,
        denial: Verdict | None = None,
        effect_id: str = "",
        publish: bool = True,
    ) -> RunnerResult:
        """One audit row and one event for everything that ends before, or
        instead of, execution.

        A denial reserves nothing — there is no effect to settle — but its
        record and its event still commit together, so nothing announces a
        transition that did not persist.
        """
        duration = _ms(started)
        used = verdict or Verdict(Decision.ALLOW, audit_outcome)
        effects = (
            sorted(e.value for e in resolved.subject.effects) if resolved else []
        )
        # Nothing was executed, so nothing outside GARIS can have moved. This is
        # the one disposition the runner may assert on its own, because it rests
        # on a fact it owns: the call boundary was never reached.
        disposition = EffectDisposition.NOT_STARTED
        with self.effects.db.transaction() as conn:
            audit_id = self.audit.stage(
                conn,
                task_id=action.task_id,
                tool=action.tool,
                intent=action.intent,
                effects=effects,
                decision=used.decision.value,
                rule=used.rule,
                params=action.params,
                outcome=audit_outcome,
                detail=f"[{disposition.value}] {detail}" if detail
                else f"[{disposition.value}]",
                duration_ms=duration,
            )
            event = self.outbox.stage(
                conn, topic, task_id=action.task_id or "",
                payload={
                    "tool": action.tool,
                    "target": name,
                    "source": source,
                    "error": detail,
                    "kind": error_kind,
                    "rule": used.rule,
                    "reason": used.reason,
                    "failure_type": failure.value,
                    "disposition": disposition.value,
                    "retryable": retryable,
                    "goal_met": False,
                    "checked": False,
                    "uncertain": False,
                    "duration_ms": duration,
                },
            )
        if publish:
            self.outbox.drain()

        return RunnerResult(
            target=name, source=source, effect_id=effect_id, ok=False,
            verification=Verification(
                goal_met=False, checked=False, reason=detail
            ),
            failure=failure, audit_id=audit_id, event_id=event.event_id,
            error=detail, error_kind=error_kind, retryable=retryable,
            disposition=disposition,
            duration_ms=duration, denial=denial,
        )


# ------------------------------------------------------------------ identity


def derive_effect_id(name: str, action: Action, subject: PolicySubject) -> str:
    """Step 6. The same intended act must resolve to the same id; two different
    acts must not.

    Effectful work gets a **deterministic** id built from the task, the step, the
    target and the arguments — so a task resumed after a crash lands on the same
    reservation and does not install, pay or send anything twice. That needs a
    step key: without one there is nothing durable to be "the same step" across a
    restart, and inventing determinism from the arguments alone would make two
    deliberate identical payments look like one retried payment.

    Reads get a **fresh** id every time, on purpose. Replaying a measurement
    hands back a stale reading dressed as a current one — the disk figure from
    ten minutes ago presented as the disk figure now. Re-measuring costs nothing
    and is the only honest answer.

    Whether something is effectful comes from declared metadata (`effects`,
    `reversible`), never from the target's name or its arguments.
    """
    if subject.effectful and action.step_key:
        seed = "|".join(
            (
                action.task_id or "",
                action.step_key,
                name,
                arguments_hash(action.params),
            )
        )
        return "eff-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:28]
    return "inv-" + uuid.uuid4().hex[:28]


def _goal_of(
    executor: TargetExecutor, resolved: Resolved, params: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Ask the target what it wants, and never let the answer break the run.

    A goal is bookkeeping for a recovery that may never be needed. An executor
    that raises while describing its own intent must not stop the work from
    happening — the effect is still reserved, and recovery will simply have
    nothing to compare against, which is the state everything already handles.
    """
    try:
        wanted = executor.goal(resolved, params)
    except Exception:
        return None
    return dict(wanted) if wanted else None


def decide_disposition(
    *,
    executor_started: bool,
    effectful: bool,
    output: ExecutionOutput,
) -> EffectDisposition:
    """Did the world move? Answered from facts, never from wording.

    The order matters. If the call boundary was never crossed, nothing outside
    GARIS can have changed, full stop. If it was, an executor's own structured
    declaration is believed — that is the only way `NOT_APPLIED` is ever
    reached, because it is a claim about the world that only the thing touching
    the world can make. Failing that, a read has nothing to apply, and anything
    effectful that got past the boundary is `UNKNOWN` whether it succeeded,
    failed politely or raised.

    `APPLIED` covers the checked goal failure: asked for 30%, measured 33%. The
    effect happened. It was simply not what was wanted, and that is a question
    for the verifier, not for this function.
    """
    if not executor_started:
        return EffectDisposition.NOT_STARTED
    if output.disposition is not None:
        return output.disposition
    if not effectful:
        # A read changes nothing by construction, so a failed read is free to
        # be attempted again.
        return EffectDisposition.NOT_APPLIED
    if output.ok:
        return EffectDisposition.APPLIED
    return EffectDisposition.UNKNOWN


def child_step_key(parent: str | None, tool: str, ordinal: int) -> str:
    """A distinct, derived key for a tool that reached for another tool.

    Never the parent's own key: sharing it would make the child's effect
    collide with the parent's, and a resumed task would then replay the wrong
    one. Derived rather than random so the same nested call in a resumed task
    still resolves to the same identity.
    """
    return f"{parent or 'root'}/{tool}#{ordinal}"


# ------------------------------------------------------------------ helpers


def _attach_evidence(
    verification: Verification, records: Sequence[EvidenceRecord]
) -> Verification:
    if not records:
        return verification
    return Verification(
        goal_met=verification.goal_met,
        checked=verification.checked,
        uncertain=verification.uncertain,
        checked_by=verification.checked_by,
        reason=verification.reason,
        unmet=verification.unmet,
        evidence_ids=tuple(r.id for r in records),
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _summarise(value: Any, limit: int = 200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, dict):
        text = ", ".join(f"{k}={_short(v)}" for k, v in list(value.items())[:6])
    elif isinstance(value, (list, tuple)):
        text = f"{len(value)} pozycji"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(value: Any, limit: int = 40) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_RESULT_FIELDS = tuple(
    f for f in RunnerResult.__dataclass_fields__.values()
    if f.name not in ("approval_request_id", "approval_prompt")
)

CancelCheck = Callable[[], bool]

__all__ = [
    "AUDIT_APPROVAL_DENIED",
    "AUDIT_AWAITING_APPROVAL",
    "AUDIT_CANCELLED",
    "AUDIT_ERROR",
    "AUDIT_OK",
    "AUDIT_PERSISTENCE_FAILED",
    "AUDIT_POLICY_DENIED",
    "AUDIT_POSTCONDITION_FAILED",
    "AUDIT_REPLAYED",
    "AUDIT_UNCERTAIN",
    "CapabilityRunner",
    "EffectDisposition",
    "ExecutionOutput",
    "Resolved",
    "RunnerResult",
    "TargetExecutor",
    "child_step_key",
    "decide_disposition",
    "derive_effect_id",
]
