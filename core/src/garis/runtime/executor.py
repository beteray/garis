"""The single controlled execution path.

Every capability GARIS has — reading a file, restarting a service, paying for
something, driving a server — arrives here as an ``Action`` and passes the same
seven gates in the same order:

    resolve → validate → policy → approval → lease → run → audit

The model proposes; this function decides. Nothing bypasses it, including tools
calling other tools, because ``ToolContext.perform`` routes back through here.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..errors import (
    ApprovalDenied,
    ApprovalRequired,
    ExecutionError,
    GarisError,
    LeaseTimeout,
    PolicyDenied,
    StoreError,
    ToolNotFound,
    ToolValidationError,
    Unsupported,
)
from ..events import EventBus, Topic
from ..net import HttpClient
from ..paths import Paths
from ..store import Database
from .action import Action, ActionResult, Effect
from .approvals import ApprovalBroker
from .audit import AuditLog, redact
from .context import ToolContext
from .leases import LeaseManager, LeaseMode
from .policy import Decision, PolicyEngine, Verdict
from .registry import ToolRegistry, ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..memory import MemoryService
    from ..vault import Vault

# Effects that mean "no one else may touch this at the same time".
_MUTATING = frozenset(
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

    # ------------------------------------------------------------------- public

    async def perform(self, action: Action) -> ActionResult:
        """Run one action.

        Returns a failed ``ActionResult`` for anything the agent can work around
        (unknown tool, bad parameters, tool error, timeout). Raises only when the
        flow itself must stop: :class:`ApprovalRequired`, :class:`PolicyDenied`,
        :class:`ApprovalDenied`.
        """
        started = time.monotonic()

        # 1. resolve ---------------------------------------------------------
        try:
            spec = self.registry.get(action.tool)
        except ToolNotFound as exc:
            return self._reject(action, None, "unknown_tool", exc, kind="not_found",
                                retryable=False, started=started)

        # 2. validate --------------------------------------------------------
        try:
            spec.require_supported()
            action.params = spec.validate(action.params)
        except Unsupported as exc:
            return self._reject(action, spec, "unsupported", exc, kind="unsupported",
                                retryable=False, started=started)
        except ToolValidationError as exc:
            return self._reject(action, spec, "invalid_params", exc, kind="invalid",
                                retryable=True, started=started)

        # 3. policy ----------------------------------------------------------
        verdict = self.policy.evaluate(spec, action)
        if verdict.decision is Decision.DENY:
            self.audit.record(action, spec, verdict, outcome="denied", detail=verdict.reason)
            self.bus.emit(
                Topic.ACTION_DENIED,
                task_id=action.task_id,
                tool=action.tool,
                rule=verdict.rule,
                reason=verdict.reason,
            )
            raise PolicyDenied(
                f"Odmowa ({verdict.rule}): {verdict.reason}",
                rule=verdict.rule,
                user_message="Tego nie zrobię — dotyczy moich własnych zabezpieczeń.",
            )

        # 4. approval --------------------------------------------------------
        if verdict.decision is Decision.CONFIRM:
            self._require_approval(action, spec, verdict)

        # 5. lease -----------------------------------------------------------
        keys = spec.resource_keys(action.params)
        mode = LeaseMode.EXCLUSIVE if (spec.effects & _MUTATING) else LeaseMode.SHARED
        holder = action.task_id or f"action:{action.id}"
        try:
            lease = await self.leases.acquire(
                keys, holder=holder, mode=mode, timeout=spec.timeout
            )
        except LeaseTimeout as exc:
            return self._reject(action, spec, "lease_timeout", exc, kind="busy",
                                retryable=True, started=started, verdict=verdict)

        # 6. run -------------------------------------------------------------
        try:
            return await self._invoke(action, spec, verdict, started)
        finally:
            lease.release()

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

    # ------------------------------------------------------------------ private

    def _require_approval(self, action: Action, spec: ToolSpec, verdict: Verdict) -> None:
        fingerprint = action.fingerprint()

        denied = self.approvals.denied_for(fingerprint)
        if denied is not None:
            self.approvals.consume(denied.id)
            self.audit.record(action, spec, verdict, outcome="rejected_by_user")
            raise ApprovalDenied(
                f"Użytkownik odrzucił operację {action.tool}",
                user_message="Dobrze, nie robię tego.",
            )

        granted = self.approvals.granted_for(fingerprint)
        if granted is not None:
            # One yes authorises exactly one execution of exactly this operation.
            self.approvals.consume(granted.id)
            return

        request = self.approvals.request(
            action, spec, verdict, redacted_params=redact(action.params)
        )
        self.audit.record(
            action, spec, verdict, outcome="awaiting_approval", detail=request.prompt
        )
        raise ApprovalRequired(
            f"Operacja {action.tool} wymaga zgody użytkownika",
            request_id=request.id,
            prompt=request.prompt,
        )

    async def _invoke(
        self, action: Action, spec: ToolSpec, verdict: Verdict, started: float
    ) -> ActionResult:
        # Secrets are substituted here, at the last moment, and never earlier:
        # the plan, the events and the audit row all keep the vault:// reference.
        try:
            resolved, used_secrets = (
                self.vault.resolve_tree(action.params) if self.vault else (action.params, [])
            )
        except StoreError as exc:
            return self._reject(action, spec, "missing_secret", exc, kind="missing_secret",
                                retryable=False, started=started, verdict=verdict)

        timeout = spec.timeout
        deadline = time.monotonic() + timeout
        ctx = ToolContext(
            runtime=self,
            action=action,
            spec=spec,
            paths=self.paths,
            config=self.config,
            bus=self.bus,
            db=self.db,
            http=self.http,
            vault=self.vault,
            memory=self.memory,
            deadline=deadline,
        )

        self.bus.emit(
            Topic.ACTION_STARTED,
            task_id=action.task_id,
            tool=action.tool,
            intent=action.intent,
            effects=sorted(e.value for e in spec.effects),
        )

        try:
            value = await asyncio.wait_for(spec.handler(ctx, **resolved), timeout=timeout)
        except TimeoutError:
            return self._reject(
                action,
                spec,
                "timeout",
                ExecutionError(f"{spec.name}: przekroczono {timeout:.0f} s", tool=spec.name),
                kind="timeout",
                retryable=True,
                started=started,
                verdict=verdict,
            )
        except asyncio.CancelledError:
            self.audit.record(action, spec, verdict, outcome="cancelled",
                              duration_ms=_ms(started))
            raise
        except (ApprovalRequired, PolicyDenied, ApprovalDenied):
            raise  # a nested action needs the user; let it bubble to the task
        except GarisError as exc:
            retryable = getattr(exc, "retryable", True)
            return self._reject(action, spec, "error", exc, kind="error",
                                retryable=retryable, started=started, verdict=verdict)
        except Exception as exc:
            return self._reject(
                action, spec, "crash",
                ExecutionError(f"{spec.name}: {type(exc).__name__}: {exc}", tool=spec.name),
                kind="crash", retryable=True, started=started, verdict=verdict,
            )
        finally:
            del used_secrets  # values go out of scope with the local frame

        duration = _ms(started)
        result = ActionResult.success(action, value, duration_ms=duration)
        self.audit.record(action, spec, verdict, outcome="ok", duration_ms=duration,
                          detail=_summarise(value))
        self.bus.emit(
            Topic.ACTION_FINISHED,
            task_id=action.task_id,
            tool=action.tool,
            duration_ms=duration,
            summary=_summarise(value),
        )
        return result

    def _reject(
        self,
        action: Action,
        spec: ToolSpec | None,
        outcome: str,
        exc: GarisError,
        *,
        kind: str,
        retryable: bool,
        started: float,
        verdict: Verdict | None = None,
    ) -> ActionResult:
        duration = _ms(started)
        used = verdict or Verdict(Decision.ALLOW, outcome)
        self.audit.record(action, spec, used, outcome="error", detail=str(exc),
                          duration_ms=duration)
        self.bus.emit(
            Topic.ACTION_FAILED,
            task_id=action.task_id,
            tool=action.tool,
            error=str(exc),
            kind=kind,
        )
        return ActionResult.failure(
            action, str(exc), kind=kind, retryable=retryable, detail=outcome,
            duration_ms=duration,
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


__all__ = ["Runtime"]
