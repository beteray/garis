"""What GARIS can be asked to do, declared with its proof obligations attached.

A tool says what it *is* — a name, parameters, effects. A capability says what it
means to have *succeeded*, and refuses to claim success without the evidence its
own declaration demands. That difference is the whole point of this layer.

The failure it exists to prevent has already happened once here: a task ran a
real tool, got real numbers back, and was reported as verified by a stub that had
never looked at them. Fail-closed semantics fixed the reporting. This fixes the
shape: a capability cannot be registered without a verifier, the verifier is
handed structured evidence rather than prose, and "verified" is a value the
verifier returns rather than a mood the pipeline arrives in.

Each capability declares:

  * a **stable id** (`windows.audio.set`) and a **version**, because a workflow
    recorded against v1 must not be silently replayed against different
    semantics;
  * **typed input and output**, so a planner cannot invent parameters and a
    presenter cannot read fields that were never promised;
  * **risk** and **permission**, which the policy layer gates on — declared by
    the capability, never by whoever calls it;
  * whether it **needs a model** and whether it **needs approval**;
  * an **executor** that does the work and an independent **verifier** that
    checks the postcondition;
  * the **platforms** it runs on, so an unsupported host is a fact known before
    anything is planned rather than an exception halfway through;
  * an **evidence schema**: the fields the verifier requires. Missing evidence is
    an unverified result, never a successful one.

Nothing here calls a model. A capability is deterministic machinery; the model's
only job is choosing which one to invoke and with what.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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


class Permission(StrEnum):
    """What the capability needs to be allowed to touch."""

    NONE = "none"
    SYSTEM_READ = "system.read"
    AUDIO = "audio"
    DESKTOP = "desktop"           # windows, monitors, input
    PROCESS = "process"           # start or stop programs
    FILES = "files"
    NETWORK = "network"


@dataclass(frozen=True, slots=True)
class Field:
    """One field of a capability's input or output."""

    name: str
    type: str                     # int | float | str | bool | list | dict
    description: str = ""
    required: bool = True
    #: For evidence fields: the value must be present *and* pass this.
    check: Callable[[Any], bool] | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """What actually happened, in a shape a verifier can be strict about."""

    ok: bool
    #: The capability's own structured result. Never prose.
    value: Any = None
    #: What was measured *after* the action, for the verifier to check against.
    evidence: Mapping[str, Any] = field(default_factory=dict)
    #: Why not, when `ok` is false. Shown to the user, so it says what to do.
    error: str = ""
    #: Set when the capability changed something and could not confirm the
    #: change. Neither success nor failure: the honest third answer, and the
    #: reason a caller must not retry blindly.
    uncertain: bool = False


@dataclass(frozen=True, slots=True)
class Verdict:
    """The verifier's answer. `ok` alone never licenses the word "sprawdzone"."""

    ok: bool
    checked: bool
    note: str = ""
    missing: tuple[str, ...] = ()

    @classmethod
    def unchecked(cls, why: str) -> Verdict:
        return cls(ok=False, checked=False, note=why)


Executor = Callable[["Invocation"], Awaitable[Outcome]]
Verifier = Callable[["Invocation", Outcome], Awaitable[Verdict]]


@dataclass(frozen=True, slots=True)
class Invocation:
    """One call: the arguments, and the context the executor may use."""

    capability: str
    args: Mapping[str, Any] = field(default_factory=dict)
    #: Stable across a retry of the *same* intended effect. Two invocations
    #: sharing an effect id are the same act, not two of them — which is what
    #: stops a crash-and-resume from launching Discord twice.
    effect_id: str = ""

    def get(self, name: str, default: Any = None) -> Any:
        return self.args.get(name, default)


@dataclass(frozen=True, slots=True)
class Capability:
    id: str
    version: int
    summary: str
    risk: Risk
    permission: Permission
    executor: Executor
    verifier: Verifier
    inputs: tuple[Field, ...] = ()
    outputs: tuple[Field, ...] = ()
    #: The fields the verifier requires in `Outcome.evidence`. A capability with
    #: an empty schema cannot be verified, and says so rather than passing.
    evidence: tuple[Field, ...] = ()
    platforms: tuple[str, ...] = ("win32", "linux", "darwin")
    needs_model: bool = False
    needs_approval: bool = False
    #: Off the planner's menu until someone turns it on. A capability layer that
    #: exposes everything the moment it exists is a bigger attack surface than
    #: the tool registry it was meant to discipline.
    exposed: bool = False

    def supported_here(self) -> bool:
        return sys.platform in self.platforms

    def validate(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Coerce and check arguments, or raise. Unknown keys are dropped, not
        forwarded: a planner that invents `force=true` must not reach an
        executor that happens to accept it."""
        out: dict[str, Any] = {}
        for spec in self.inputs:
            if spec.name not in args:
                if spec.required:
                    raise ValueError(f"{self.id}: brakuje parametru {spec.name!r}")
                continue
            value = args[spec.name]
            out[spec.name] = _coerce(self.id, spec, value)
        return out

    def missing_evidence(self, outcome: Outcome) -> tuple[str, ...]:
        """Which declared evidence fields are absent or fail their own check."""
        gaps: list[str] = []
        for spec in self.evidence:
            if spec.name not in outcome.evidence:
                if spec.required:
                    gaps.append(spec.name)
                continue
            value = outcome.evidence[spec.name]
            if spec.check is not None and not spec.check(value):
                gaps.append(spec.name)
        return tuple(gaps)


def _coerce(where: str, spec: Field, value: Any) -> Any:
    try:
        match spec.type:
            case "int":
                return int(value)
            case "float":
                return float(value)
            case "bool":
                return bool(value)
            case "str":
                return value if isinstance(value, str) else str(value)
            case "list":
                if not isinstance(value, (list, tuple)):
                    raise TypeError(type(value).__name__)
                return list(value)
            case "dict":
                if not isinstance(value, dict):
                    raise TypeError(type(value).__name__)
                return dict(value)
            case _:
                return value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: {spec.name} — {exc}") from exc


async def always_unchecked(_: Invocation, __: Outcome) -> Verdict:
    """A verifier for capabilities whose postcondition nobody has written yet.

    Deliberately explicit and deliberately awkward to reach for: it returns
    "not checked", so anything using it can finish but can never be reported as
    verified. The alternative — letting the field be optional — is how "no
    verifier" quietly becomes "verified".
    """
    return Verdict.unchecked("Nie ma sprawdzenia po fakcie dla tej zdolności.")


def evidence_verifier(note: str = "Sprawdziłem wynik po fakcie.") -> Verifier:
    """The common case: the declared evidence is present and passes its checks.

    Not a rubber stamp — the checks live on the fields, so a capability that
    declares `volume_after` with a tolerance check gets a real postcondition out
    of this, while one that declares nothing gets `unchecked`.
    """

    async def verify(invocation: Invocation, outcome: Outcome) -> Verdict:
        from .registry import REGISTRY

        capability = REGISTRY.get(invocation.capability)
        if not capability.evidence:
            return Verdict.unchecked(
                "Ta zdolność nie deklaruje żadnych dowodów do sprawdzenia."
            )
        if not outcome.ok:
            return Verdict(ok=False, checked=True, note=outcome.error or "Nie powiodło się.")
        gaps = capability.missing_evidence(outcome)
        if gaps:
            return Verdict(
                ok=False,
                checked=True,
                note="Brakuje dowodów wykonania: " + ", ".join(gaps),
                missing=gaps,
            )
        return Verdict(ok=True, checked=True, note=note)

    return verify


__all__ = [
    "Capability",
    "Executor",
    "Field",
    "Invocation",
    "Outcome",
    "Permission",
    "Risk",
    "Verdict",
    "Verifier",
    "always_unchecked",
    "evidence_verifier",
]
