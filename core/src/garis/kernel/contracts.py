"""The vocabulary every layer shares, and nothing else.

This module exists because GARIS grew two of everything. Two results
(``ActionResult`` and ``Outcome``), two verdicts (``Verification`` and
``Verdict``), two idempotence mechanisms, two execution paths — one with policy,
approvals and audit, one with typed evidence and a mandatory verifier, neither
with both. Merging the paths starts with merging the words.

Nothing here executes anything, touches a database, calls a provider or knows
what Windows is. It is the contract the kernel, the capability layer, the
platform adapters and the orchestration all agree on, and it depends on nothing
in the codebase except the error base class.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..errors import GarisError

# --------------------------------------------------------------------- failure


class Failure(StrEnum):
    """Why something did not happen, as a value rather than a sentence.

    Control flow used to read error messages. That works until a message is
    reworded for the user — which happens constantly, because these messages are
    the product — and then a branch silently stops matching. Every decision the
    engine makes about a failure is made on this enum.
    """

    UNSUPPORTED_PLATFORM = "unsupported_platform"
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_DENIED = "approval_denied"
    EXECUTOR_FAILED = "executor_failed"
    #: The action ran and the postcondition says it did not achieve the goal.
    #: Different from EXECUTOR_FAILED: the call worked, the world did not move.
    POSTCONDITION_FAILED = "postcondition_failed"
    #: Something may have changed and nobody can tell. Never retried blindly.
    EFFECT_UNCERTAIN = "effect_uncertain"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CANCELLED = "cancelled"
    PERSISTENCE_FAILED = "persistence_failed"


class Effect(StrEnum):
    """What an action does to the world. Declared per tool and per capability.

    Lives here rather than in ``runtime`` because both the tool layer and the
    capability layer must speak it, and neither may import the other. The old
    home re-exports it, so every existing ``from garis.runtime import Effect``
    keeps working.
    """

    READ = "read"                      # inspect files, processes, screen text
    WRITE = "write"                    # create or modify recoverable data
    DELETE_PERMANENT = "delete_permanent"   # irreversible loss
    EXEC = "exec"                      # run a program or script
    INSTALL = "install"                # add or update software
    NETWORK = "network"                # outbound traffic
    PAYMENT = "payment"                # spend money
    PUBLISH = "publish"                # make something publicly visible
    SEND_MESSAGE = "send_message"      # speak as the user to another person
    CREDENTIALS = "credentials"        # touch passwords, tokens, logins
    SYSTEM_CONFIG = "system_config"    # registry, services, firewall, policies
    INPUT_CONTROL = "input_control"    # drive mouse and keyboard
    CAPTURE = "capture"                # screen, microphone, camera
    ELEVATE = "elevate"                # require administrator rights
    REMOTE = "remote"                  # act on another machine

    @property
    def label_pl(self) -> str:
        return _EFFECT_LABELS_PL.get(self, self.value)


_EFFECT_LABELS_PL: dict[Effect, str] = {
    Effect.READ: "odczyt",
    Effect.WRITE: "zapis",
    Effect.DELETE_PERMANENT: "trwałe usunięcie",
    Effect.EXEC: "uruchomienie programu",
    Effect.INSTALL: "instalacja",
    Effect.NETWORK: "połączenie sieciowe",
    Effect.PAYMENT: "płatność",
    Effect.PUBLISH: "publikacja",
    Effect.SEND_MESSAGE: "wysłanie wiadomości",
    Effect.CREDENTIALS: "dane logowania",
    Effect.SYSTEM_CONFIG: "zmiana ustawień systemu",
    Effect.INPUT_CONTROL: "sterowanie myszą i klawiaturą",
    Effect.CAPTURE: "nagrywanie ekranu lub mikrofonu",
    Effect.ELEVATE: "uprawnienia administratora",
    Effect.REMOTE: "działanie na innym urządzeniu",
}

#: Effects that mean "this changed something outside GARIS". An operation
#: declaring any of these gets a *deterministic* effect id, so a task resumed
#: after a crash resolves to the same reservation instead of doing it twice.
#: Reads are deliberately absent: replaying a measurement would hand back a
#: stale reading, and re-measuring is both free and more truthful.
EFFECTFUL: frozenset[Effect] = frozenset(
    {
        Effect.WRITE,
        Effect.DELETE_PERMANENT,
        Effect.EXEC,
        Effect.INSTALL,
        Effect.PAYMENT,
        Effect.PUBLISH,
        Effect.SEND_MESSAGE,
        Effect.CREDENTIALS,
        Effect.SYSTEM_CONFIG,
        Effect.INPUT_CONTROL,
        Effect.REMOTE,
    }
)


class Risk(StrEnum):
    """How much a failed or misjudged run can cost.

    Not a synonym for "effects": reading a window title is `READ` even though it
    touches the desktop, and setting the volume is `REVERSIBLE` even though it
    changes the machine, because it can be put back.
    """

    READ = "read"                 # observes, changes nothing
    REVERSIBLE = "reversible"     # changes something that can be put back
    DISRUPTIVE = "disruptive"     # interrupts the person's work
    IRREVERSIBLE = "irreversible" # cannot be undone

    @property
    def mutating(self) -> bool:
        """Whether a run of this is expected to change the world at all."""
        return self is not Risk.READ


class Permission(StrEnum):
    """What a capability needs to be allowed to touch.

    Deliberately **not** a description of the effect. ``FILES`` does not say
    whether a name is being read or a directory permanently deleted; ``PROCESS``
    does not say whether a list is being taken or a program killed. Effects are
    declared separately and explicitly, because inferring one from the other is
    how a delete gets gated like a read.
    """

    NONE = "none"
    SYSTEM_READ = "system.read"
    AUDIO = "audio"
    DESKTOP = "desktop"           # windows, monitors, input
    PROCESS = "process"           # start or stop programs
    FILES = "files"
    NETWORK = "network"


class CapabilityError(GarisError):
    """A failure with a type attached, raised where raising is the honest move."""

    def __init__(
        self,
        failure: Failure,
        detail: str = "",
        *,
        user_message: str = "",
        uncertain: bool = False,
    ) -> None:
        super().__init__(detail or failure.value, user_message=user_message or detail)
        self.failure = failure
        self.detail = detail
        self.uncertain = uncertain


# ---------------------------------------------------------------- verification


@dataclass(frozen=True, slots=True)
class Verification:
    """Whether the goal was met, and whether anyone actually checked.

    Three independent facts, deliberately not collapsible:

    * ``goal_met=False, checked=True, uncertain=False`` — checked, and it failed.
    * ``goal_met=False, checked=False, uncertain=True`` — nobody can currently
      tell what the world looks like. Not a failure; not a success.
    * ``goal_met=True, checked=False`` — plausible, unproven, and never
      ``verified``.

    Collapsing these is the original defect: a stub answered "ok" and the report
    printed "sprawdzone" because `ok` was the only field there was.
    """

    goal_met: bool
    checked: bool = False
    uncertain: bool = False
    #: "rules" — a deterministic postcondition; "model" — a real provider judged
    #: the evidence; "none" — nothing checked anything.
    checked_by: str = "none"
    reason: str = ""
    unmet: tuple[str, ...] = ()
    #: Which recorded evidence this verdict rests on.
    evidence_ids: tuple[str, ...] = ()

    @property
    def verified(self) -> bool:
        """The only definition of the word, anywhere in GARIS."""
        return self.goal_met and self.checked and not self.uncertain

    # Read-only aliases, kept because the report and the API have spoken this
    # way since 0.1.0 and renaming them would be churn with no reader benefit.
    @property
    def ok(self) -> bool:
        return self.goal_met

    @property
    def note(self) -> str:
        return self.reason

    @classmethod
    def unchecked(cls, why: str, *, goal_met: bool = False) -> Verification:
        return cls(goal_met=goal_met, checked=False, reason=why)

    @classmethod
    def uncertain_effect(cls, why: str) -> Verification:
        return cls(goal_met=False, checked=False, uncertain=True, reason=why,
                   checked_by="rules")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.goal_met,
            "goal_met": self.goal_met,
            "checked": self.checked,
            "uncertain": self.uncertain,
            "checked_by": self.checked_by,
            "note": self.reason,
            "reason": self.reason,
            "unmet": list(self.unmet),
            "evidence_ids": list(self.evidence_ids),
            "verified": self.verified,
        }


# ------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """What was measured, by whom, when — the thing a verdict must rest on."""

    id: str
    capability: str
    version: int
    at: float
    fields: Mapping[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @classmethod
    def new(
        cls,
        capability: str,
        version: int,
        fields: Mapping[str, Any],
        *,
        missing: Sequence[str] = (),
    ) -> EvidenceRecord:
        return cls(
            id=uuid.uuid4().hex[:16],
            capability=capability,
            version=version,
            at=time.time(),
            fields=dict(fields),
            missing=tuple(missing),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "capability": self.capability,
            "version": self.version,
            "at": self.at,
            "fields": dict(self.fields),
            "missing": list(self.missing),
        }


#: Field names that must never leave the engine in an evidence summary. Matched
#: as substrings against the *key*, because `authorization_header` and
#: `auth_header` are the same mistake spelled differently.
SECRET_HINTS = (
    "secret", "token", "password", "passwd", "haslo", "key", "klucz",
    "authorization", "auth", "credential", "cookie", "session", "bearer",
    "vault://", "private",
)

#: Values longer than this are summarised rather than sent. A file's contents
#: are evidence to the verifier and a leak to the window.
MAX_SUMMARY_CHARS = 200


def redact(fields: Mapping[str, Any]) -> dict[str, Any]:
    """An evidence summary safe to hand to the frontend.

    Names that look like credentials are replaced, not truncated: half a token
    is still a token. Long values are summarised by shape, because "a 4 kB
    string" tells the window everything it needs and nothing it should not have.
    """
    out: dict[str, Any] = {}
    for name, value in fields.items():
        lowered = str(name).lower()
        if any(hint in lowered for hint in SECRET_HINTS):
            out[name] = "[ukryte]"
            continue
        if isinstance(value, str):
            if any(hint in value.lower() for hint in ("vault://", "bearer ")):
                out[name] = "[ukryte]"
            elif len(value) > MAX_SUMMARY_CHARS:
                out[name] = f"[tekst, {len(value)} znaków]"
            else:
                out[name] = value
        elif isinstance(value, (int, float, bool)) or value is None:
            out[name] = value
        elif isinstance(value, (list, tuple)):
            out[name] = f"[{len(value)} pozycji]"
        elif isinstance(value, Mapping):
            out[name] = redact(value)
        else:
            out[name] = f"[{type(value).__name__}]"
    return out


# -------------------------------------------------------------------- context


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Something worth saying while a capability is still running."""

    message: str
    silent: bool = True


class RuntimeProfile(StrEnum):
    """Which world this process is in. Not a flag, and not an environment guess."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"
    TEST = "test"
    FIXTURE = "fixture"

    @property
    def allows_doubles(self) -> bool:
        """Whether stand-ins may exist at all. Production: never."""
        return self in (RuntimeProfile.TEST, RuntimeProfile.FIXTURE)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """What an executor is allowed to know.

    Deliberately without a `perform` callback. Handing executors a way to call
    arbitrary tools would rebuild the second execution path *inside* the runner:
    a capability could act with no policy check of its own, no audit row and no
    effect id. Legacy tools reach the unified path through `LegacyToolExecutor`,
    once, with the policy and audit wrapped around it exactly one layer up.
    """

    task_id: str = ""
    step_key: str = ""
    effect_id: str = ""
    cancelled: Callable[[], bool] = lambda: False
    progress: Callable[[ProgressEvent], None] = lambda _: None
    runtime_profile: RuntimeProfile = RuntimeProfile.PRODUCTION
    #: The invocation this one was started from, when a tool reached for another
    #: tool. Recorded so a tree of effects can be read back as a tree rather than
    #: as an unordered pile that all shares one task id.
    parent_effect_id: str = ""
    depth: int = 0

    def say(self, message: str, *, silent: bool = True) -> None:
        self.progress(ProgressEvent(message, silent=silent))


#: How deep tools may call tools. A tool that reaches for a tool is legitimate;
#: a cycle of them is a runaway that would otherwise only stop when the database
#: filled up. The number is arbitrary and deliberately small — nothing in this
#: codebase nests more than twice.
MAX_NESTING_DEPTH = 8


# --------------------------------------------------------------- policy input


@dataclass(frozen=True, slots=True)
class PolicyEstimate:
    """What an operation is expected to cost, computed before policy runs.

    Normalised here so ``PolicyEngine`` never calls back into a ``ToolSpec``
    method: once a subject exists, policy reads data and nothing else. The
    adapter that built the subject is the only thing that knew how to ask.
    """

    duration_seconds: float | None = None
    cost: float | None = None
    affected_items: int | None = None
    bytes_moved: int = 0


@dataclass(frozen=True, slots=True)
class PolicySubject:
    """One thing policy can be asked about, whatever layer it came from.

    Data only. No handler, no executor, no registry, no runtime object — a
    subject can be logged, compared and constructed in a test without dragging
    the machinery that produced it. ``source`` says which adapter built it, so a
    denial can name the layer without policy having to branch on type.
    """

    id: str
    risk: Risk
    permissions: tuple[Permission, ...]
    requires_approval: bool
    source: str                       # "legacy_tool" | "capability"
    name: str = ""
    category: str = "general"
    effects: frozenset[Effect] = frozenset()
    reversible: bool = True
    trusted_source: bool = True
    danger_note: str = ""

    @property
    def effectful(self) -> bool:
        """Whether this changes the world, from declared metadata alone."""
        return bool(self.effects & EFFECTFUL) or not self.reversible


# --------------------------------------------------------------------- targets


@dataclass(frozen=True, slots=True)
class ToolTarget:
    """A legacy tool, run through the adapter."""

    tool: str

    @property
    def name(self) -> str:
        return self.tool


@dataclass(frozen=True, slots=True)
class CapabilityTarget:
    """A native capability, run directly by the runner."""

    capability: str

    @property
    def name(self) -> str:
        return self.capability


StepTarget = ToolTarget | CapabilityTarget


def target_from_dict(data: Mapping[str, Any]) -> StepTarget:
    """Exactly one of `tool` or `capability`, never both, never neither.

    Enforced when a plan is built and again when one is loaded, because a row
    written by a future version — or by a model that learned to emit both — must
    fail loudly rather than pick one silently.
    """
    tool = str(data.get("tool") or "").strip()
    capability = str(data.get("capability") or "").strip()
    if tool and capability:
        raise CapabilityError(
            Failure.INVALID_INPUT,
            f"krok wskazuje jednocześnie narzędzie {tool!r} i zdolność {capability!r}",
        )
    if capability:
        return CapabilityTarget(capability)
    if tool:
        return ToolTarget(tool)
    raise CapabilityError(Failure.INVALID_INPUT, "krok nie wskazuje ani narzędzia, ani zdolności")


def target_to_dict(target: StepTarget) -> dict[str, str]:
    """The persisted shape: both columns present, exactly one filled."""
    if isinstance(target, CapabilityTarget):
        return {"tool": "", "capability": target.capability}
    return {"tool": target.tool, "capability": ""}


# --------------------------------------------------------------------- events


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """A fact that has been persisted, on its way to whoever is listening.

    `sequence` orders events within one task; `event_id` lets a consumer
    deduplicate, because delivery is at-least-once and this codebase will not
    claim otherwise.
    """

    event_id: str
    topic: str
    task_id: str
    sequence: int
    occurred_at: float
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls, topic: str, task_id: str, sequence: int, payload: Mapping[str, Any]
    ) -> RuntimeEvent:
        return cls(
            event_id=uuid.uuid4().hex[:16],
            topic=topic,
            task_id=task_id,
            sequence=sequence,
            occurred_at=time.time(),
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "topic": self.topic,
            "task_id": self.task_id,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            **dict(self.payload),
        }


# ------------------------------------------------------------------ snapshots


@dataclass(frozen=True, slots=True)
class TaskSnapshot:
    """What a task looks like to anything outside the kernel.

    Canonical state, the step actually running, the structured result, a
    *redacted* evidence summary, and every claim separated from every other:
    `verified`, `uncertain` and `failure` are different questions.
    """

    id: str
    state: str
    goal: str
    verified: bool = False
    uncertain: bool = False
    failure: Failure | None = None
    needs_approval: bool = False
    current_step: str = ""
    answer: str = ""
    evidence: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "goal": self.goal,
            "verified": self.verified,
            "uncertain": self.uncertain,
            "failure_type": self.failure.value if self.failure else "",
            "needs_approval": self.needs_approval,
            "current_step": self.current_step,
            "answer": self.answer,
            "evidence_summary": [dict(item) for item in self.evidence],
        }


__all__ = [
    "EFFECTFUL",
    "MAX_NESTING_DEPTH",
    "MAX_SUMMARY_CHARS",
    "SECRET_HINTS",
    "CapabilityError",
    "CapabilityTarget",
    "Effect",
    "EvidenceRecord",
    "ExecutionContext",
    "Failure",
    "Permission",
    "PolicyEstimate",
    "PolicySubject",
    "ProgressEvent",
    "Risk",
    "RuntimeEvent",
    "RuntimeProfile",
    "StepTarget",
    "TaskSnapshot",
    "ToolTarget",
    "Verification",
    "redact",
    "target_from_dict",
    "target_to_dict",
]
