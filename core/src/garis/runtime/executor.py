"""The public door onto the one execution path.

Everything GARIS does still arrives here as an ``Action``, and callers still see
exactly what they always saw: an ``ActionResult`` for anything the agent can work
around, and a raised :class:`ApprovalRequired`, :class:`PolicyDenied` or
:class:`ApprovalDenied` when the flow itself must stop.

What changed is what happens underneath. This class no longer *does* any of it.
It builds a target and an execution context, calls
:class:`~garis.runtime.runner.CapabilityRunner` once, and translates the
structured result back into the vocabulary its callers have spoken since 0.1.0.
Policy, approvals, leases, effect identity, audit, evidence, verification,
persistence and event publication all live in the runner, once each, shared with
native capabilities — because two execution paths meant every guarantee was true
of only half the system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import Config
from ..errors import (
    ApprovalDenied,
    ApprovalRequired,
    PolicyDenied,
)
from ..events import EventBus
from ..kernel.contracts import (
    ExecutionContext,
    Failure,
    RuntimeProfile,
    StepTarget,
    ToolTarget,
)
from ..kernel.effects import EffectStore
from ..kernel.outbox import EventOutbox
from ..net import HttpClient
from ..paths import Paths
from ..store import Database
from .action import Action, ActionResult
from .approvals import ApprovalBroker
from .audit import AuditLog
from .leases import LeaseManager
from .legacy import LegacyToolExecutor
from .policy import PolicyEngine
from .registry import ToolRegistry
from .runner import CapabilityRunner, RunnerResult

if TYPE_CHECKING:  # pragma: no cover
    from ..memory import MemoryService
    from ..vault import Vault


class Runtime:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        approvals: ApprovalBroker,
        audit: AuditLog,
        leases: LeaseManager,
        bus: EventBus,
        paths: Paths,
        config: Config,
        db: Database,
        vault: Vault | None = None,
        memory: MemoryService | None = None,
        http: HttpClient | None = None,
        effects: EffectStore | None = None,
        outbox: EventOutbox | None = None,
        runner: CapabilityRunner | None = None,
        profile: RuntimeProfile = RuntimeProfile.PRODUCTION,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.approvals = approvals
        self.audit = audit
        self.leases = leases
        self.bus = bus
        self.paths = paths
        self.config = config
        self.db = db
        self.vault = vault
        self.memory = memory
        self.http = http or HttpClient()
        self.profile = profile
        self.effects = effects or EffectStore(db)
        self.outbox = outbox or EventOutbox(db, bus)
        self.runner = runner or CapabilityRunner(
            policy=policy, approvals=approvals, audit=audit,
            effects=self.effects, outbox=self.outbox, leases=leases,
            profile=profile,
        )
        # The runner has no idea this package exists until something tells it.
        self.runner.register_executor("legacy_tool", LegacyToolExecutor(registry, self))

    # ------------------------------------------------------------------- public

    async def perform(
        self, action: Action, context: ExecutionContext | None = None
    ) -> ActionResult:
        """Run one action.

        Returns a failed ``ActionResult`` for anything the agent can work around
        (unknown tool, bad parameters, tool error, timeout). Raises only when the
        flow itself must stop: :class:`ApprovalRequired`, :class:`PolicyDenied`,
        :class:`ApprovalDenied`.

        Three steps, deliberately: build the target, run the envelope once,
        translate the answer. Nothing is decided here.
        """
        return await self.perform_step(ToolTarget(action.tool), action, context)

    async def perform_step(
        self, target: StepTarget, action: Action, context: ExecutionContext | None = None
    ) -> ActionResult:
        """Run one action against an explicit target.

        The same envelope as :meth:`perform`, entered by callers that already
        know whether they are running a tool or a capability — the agent loop,
        once plans carry targets. Not a second path: `perform` is now written in
        terms of this one, so there is a single place where a target meets the
        runner.

        Takes a `StepTarget` rather than a plan step because `runtime` sits below
        `agent` and may not import it.
        """
        result = await self.runner.run(
            target,
            action,
            context or ExecutionContext(
                task_id=action.task_id or "",
                step_key=action.step_key or "",
                runtime_profile=self.profile,
            ),
        )
        return self._translate(action, result)

    @staticmethod
    def _translate(action: Action, result: RunnerResult) -> ActionResult:
        """The structured answer, in the words callers have always used."""
        if result.approval_request_id:
            raise ApprovalRequired(
                f"Operacja {action.tool} wymaga zgody użytkownika",
                request_id=result.approval_request_id,
                prompt=result.approval_prompt,
            )
        if result.denial is not None:
            raise PolicyDenied(
                f"Odmowa ({result.denial.rule}): {result.denial.reason}",
                rule=result.denial.rule,
                user_message="Tego nie zrobię — dotyczy moich własnych zabezpieczeń.",
            )
        if result.failure is Failure.APPROVAL_DENIED:
            raise ApprovalDenied(
                f"Użytkownik odrzucił operację {action.tool}",
                user_message="Dobrze, nie robię tego.",
            )
        if result.ok:
            return ActionResult.success(
                action, result.value, duration_ms=result.duration_ms,
                verified=result.verified if result.verification.checked else None,
                # The verifier's own sentence travels with its verdict. Without
                # it a caller knows a step missed its postcondition and cannot
                # say what it measured instead.
                detail=result.verification.reason,
            )
        return ActionResult.failure(
            action, result.error, kind=result.error_kind or "error",
            retryable=result.retryable,
            detail=result.failure.value if result.failure else "",
            duration_ms=result.duration_ms,
        )

    async def perform_tool(
        self, tool: str, /, *, intent: str = "", task_id: str | None = None, **params: Any
    ) -> ActionResult:
        """Convenience wrapper for callers that are not building plans."""
        return await self.perform(
            Action(tool=tool, params=params, intent=intent, task_id=task_id)
        )

    def describe_capabilities(self) -> dict[str, Any]:
        """What this host can actually do — feeds ``garis doctor`` and diagnostics."""
        grouped = self.registry.by_category()
        return {
            "tools_total": len(self.registry),
            "tools_available": len(self.registry.available()),
            "categories": {name: len(specs) for name, specs in sorted(grouped.items())},
            "unsupported_here": sorted(
                s.name for s in self.registry.all() if not s.supported_here()
            ),
            "policy": self.policy.describe(),
        }


__all__ = ["Runtime"]
