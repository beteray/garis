"""Goals and plans — what the agent works from.

A goal is an outcome plus the conditions that make it true. Acceptance criteria
exist so "done" is checkable instead of asserted: the verifier compares results
against them, which is the difference between GARIS reporting success and GARIS
having succeeded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..errors import GarisError
from ..kernel.contracts import (
    StepTarget,
    ToolTarget,
    target_from_dict,
    target_to_dict,
)

MAX_STEPS = 40


@dataclass(slots=True)
class Goal:
    text: str
    criteria: tuple[str, ...] = ()
    target: str = "local"            # "local" or a device name
    private: bool = False            # keep reasoning on local models if possible
    context: str = ""                # memory and situation summary for the planner
    origin: str = "user"             # user | automation | proactive

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "criteria": list(self.criteria),
            "target": self.target,
            "private": self.private,
            "origin": self.origin,
        }


@dataclass(slots=True)
class PlanStep:
    """One action in a plan, with a stable key so replays can skip it.

    `target` says *what* runs — a legacy tool or a native capability — and is
    required. There is no default: a step with no target would pass validation
    and die inside the runner, which is the wrong place to learn that a plan was
    never executable.
    """

    key: str
    target: StepTarget
    params: dict[str, Any] = field(default_factory=dict)
    purpose: str = ""
    optional: bool = False           # failure does not sink the goal
    expects: str = ""                # what a good result looks like, for the verifier

    @property
    def name(self) -> str:
        """The identifier as text — for prose, logs and anything a model reads."""
        return self.target.name

    @property
    def tool(self) -> str:
        """The legacy tool name, and only that.

        Deliberately raises for a capability instead of returning its id. A
        property that quietly answered `windows.process.list` here would let
        tool-keyed logic — `reflex.check` above all — look up a name it has never
        heard of and conclude the result cannot be verified. That failure is
        silent, survives the type checker and turns a checked success into a
        reported failure. Callers that only need a label want `name`.
        """
        if isinstance(self.target, ToolTarget):
            return self.target.tool
        raise TypeError(
            f"krok {self.key!r} wskazuje zdolność {self.target.name!r}, nie narzędzie; "
            f"użyj .target albo .name"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            # Both keys, exactly one filled — the shape `target_from_dict`
            # expects when this comes back.
            **target_to_dict(self.target),
            "params": self.params,
            "purpose": self.purpose,
            "optional": self.optional,
            "expects": self.expects,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, index: int = 0) -> PlanStep:
        return cls(
            key=str(data.get("key") or f"step-{index + 1}"),
            target=target_from_dict(data),
            params=dict(data.get("params") or {}),
            purpose=str(data.get("purpose") or data.get("why") or ""),
            optional=bool(data.get("optional", False)),
            expects=str(data.get("expects") or ""),
        )


@dataclass(slots=True)
class Plan:
    summary: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    question: str = ""               # set only when genuinely blocked
    assumptions: tuple[str, ...] = ()
    notes: str = ""
    #: Who wrote this plan: "model" or "reflex" (agent/reflex.py, no model
    #: involved). The verifier needs to know, because a reflex plan can be
    #: checked against the tool's own structured output while a model plan
    #: cannot.
    origin: str = "model"

    @property
    def blocked(self) -> bool:
        return bool(self.question) and not self.steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "question": self.question,
            "assumptions": list(self.assumptions),
            "notes": self.notes,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        raw_steps = data.get("steps") or []
        steps: list[PlanStep] = []
        rejected: list[str] = []
        for i, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                continue
            try:
                steps.append(PlanStep.from_dict(raw, index=i))
            except GarisError as exc:
                # A model naming neither a tool nor both at once is one bad step,
                # not a broken plan. The planner already drops unrunnable steps
                # and says so in `notes`; a raise here would sink the whole task
                # over a single hallucinated line.
                rejected.append(f"krok {i + 1}: {exc}")

        notes = str(data.get("notes") or "")
        if rejected:
            notes = "; ".join([*filter(None, [notes]), *rejected])

        return cls(
            summary=str(data.get("summary") or ""),
            steps=steps[:MAX_STEPS],
            question=str(data.get("question") or ""),
            assumptions=tuple(str(a) for a in (data.get("assumptions") or [])),
            notes=notes,
            # A plan parsed from a model's JSON is a model's plan, whatever the
            # JSON claims: this field is ours, not the model's, and letting a
            # completion set it to "reflex" would let it buy the deterministic
            # verifier's trust.
            origin="model",
        )

    def rekey(self) -> Plan:
        """Give every step a unique key; duplicates would collapse the journal."""
        seen: dict[str, int] = {}
        for index, step in enumerate(self.steps):
            base = _slug(step.key) or f"step-{index + 1}"
            count = seen.get(base, 0)
            seen[base] = count + 1
            step.key = base if count == 0 else f"{base}-{count + 1}"
        return self


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", text.strip().lower())
    return cleaned.strip("-")[:48]


__all__ = ["MAX_STEPS", "Goal", "Plan", "PlanStep"]
