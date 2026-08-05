"""Running a registered tool, and nothing else.

This is the narrowest file in the runtime on purpose. It resolves a name in the
tool registry, coerces the parameters the spec declares, substitutes secrets at
the last possible moment and calls the handler under its own timeout. It does
not evaluate policy, ask for approval, take a lease, reserve an effect, write an
audit row, persist anything or publish an event — all of that happens once, in
:mod:`garis.runtime.runner`, wrapped around this.

It also does not call ``Runtime.perform``. If it did, every legacy tool would
pass through the envelope twice and the guarantee the runner exists to provide —
one policy decision, one audit row, one effect per invocation — would be false
for exactly the half of the system that has the most tools in it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..errors import (
    ApprovalDenied,
    ApprovalRequired,
    GarisError,
    PolicyDenied,
    StoreError,
    ToolValidationError,
)
from ..kernel.contracts import ExecutionContext, Failure, Verification
from .action import Action
from .policy import estimate_for_tool, subject_from_tool
from .registry import ToolRegistry
from .runner import ExecutionOutput, Resolved, TargetExecutor

if TYPE_CHECKING:  # pragma: no cover
    from ..kernel.contracts import StepTarget
    from .executor import Runtime


class LegacyToolExecutor(TargetExecutor):
    """The adapter between a ``ToolSpec`` and the one envelope."""

    def __init__(self, registry: ToolRegistry, runtime: Runtime) -> None:
        self.registry = registry
        # Held only to build the `ToolContext` a handler expects. A tool that
        # reaches for another tool calls `ctx.perform`, which re-enters the
        # runner from the top with its own child step key — recursion through
        # one envelope, not a way around it.
        self.runtime = runtime

    def resolve(self, target: StepTarget, action: Action) -> Resolved:
        spec = self.registry.get(target.name)
        spec.require_supported()
        return Resolved(
            subject=subject_from_tool(spec),
            estimate=estimate_for_tool(spec, action.params),
            resource_keys=spec.resource_keys(action.params),
            timeout=spec.timeout,
            handle=spec,
        )

    def validate(self, resolved: Resolved, params: Mapping[str, Any]) -> dict[str, Any]:
        return resolved.handle.validate(dict(params))

    async def execute(
        self,
        resolved: Resolved,
        params: Mapping[str, Any],
        context: ExecutionContext,
        action: Action,
    ) -> ExecutionOutput:
        from .context import ToolContext

        spec = resolved.handle
        vault = self.runtime.vault

        # Secrets are substituted here and never earlier: the plan, the effect
        # row, the events and the audit row all keep the vault:// reference.
        try:
            resolved_params, used_secrets = (
                vault.resolve_tree(dict(params)) if vault else (dict(params), [])
            )
        except StoreError as exc:
            return ExecutionOutput(
                ok=False, error=str(exc), error_kind="missing_secret",
                retryable=False, failure=Failure.INVALID_INPUT,
            )

        timeout = spec.timeout
        ctx = ToolContext(
            runtime=self.runtime,
            action=action,
            spec=spec,
            paths=self.runtime.paths,
            config=self.runtime.config,
            bus=self.runtime.bus,
            db=self.runtime.db,
            http=self.runtime.http,
            vault=vault,
            memory=self.runtime.memory,
            deadline=time.monotonic() + timeout,
            execution=context,
        )

        try:
            value = await asyncio.wait_for(spec.handler(ctx, **resolved_params),
                                           timeout=timeout)
        except TimeoutError:
            return ExecutionOutput(
                ok=False,
                error=f"{spec.name}: przekroczono {timeout:.0f} s",
                error_kind="timeout",
                retryable=True,
                # A tool that timed out may well have done the thing and simply
                # not come back to say so.
                uncertain=resolved.subject.effectful,
                failure=Failure.EXECUTOR_FAILED,
            )
        except (ApprovalRequired, PolicyDenied, ApprovalDenied):
            # A nested tool needs the user, or was refused. That has to reach the
            # task, not be flattened into this tool's failure: a parent must not
            # hide a child's gate. Re-raised here because these are `GarisError`
            # subclasses and the handler below would otherwise swallow them.
            raise
        except ToolValidationError as exc:
            return ExecutionOutput(ok=False, error=str(exc), error_kind="invalid",
                                   retryable=True, failure=Failure.INVALID_INPUT)
        except GarisError as exc:
            return ExecutionOutput(
                ok=False, error=str(exc), error_kind="error",
                retryable=bool(getattr(exc, "retryable", True)),
                uncertain=resolved.subject.effectful,
                failure=Failure.EXECUTOR_FAILED,
            )
        finally:
            del used_secrets  # values go out of scope with the local frame

        return ExecutionOutput(ok=True, value=value)

    async def verify(
        self, resolved: Resolved, params: Mapping[str, Any], output: ExecutionOutput
    ) -> Verification:
        """Legacy tools declare no postcondition, so nothing checked this.

        Saying ``checked=False`` rather than inventing a pass is the whole
        difference between "it ran" and "sprawdzone". The goal-level verifier in
        the agent loop still runs; it just cannot borrow authority from here.
        """
        return Verification.unchecked(
            "Narzędzie nie deklaruje sprawdzenia po fakcie.", goal_met=output.ok
        )


__all__ = ["LegacyToolExecutor"]
