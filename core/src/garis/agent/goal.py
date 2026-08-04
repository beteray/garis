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
    """One action in a plan, with a stable key so replays can skip it."""

    key: str
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    purpose: str = ""
    optional: bool = False           # failure does not sink the goal
    expects: str = ""                # what a good result looks like, for the verifier

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tool": self.tool,
            "params": self.params,
            "purpose": self.purpose,
            "optional": self.optional,
            "expects": self.expects,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, index: int = 0) -> PlanStep:
        return cls(
            key=str(data.get("key") or f"step-{index + 1}"),
            tool=str(data.get("tool", "")),
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
        steps = [PlanStep.from_dict(s, index=i) for i, s in enumerate(raw_steps)
                 if isinstance(s, dict)]
        return cls(
            summary=str(data.get("summary") or ""),
            steps=steps[:MAX_STEPS],
            question=str(data.get("question") or ""),
            assumptions=tuple(str(a) for a in (data.get("assumptions") or [])),
            notes=str(data.get("notes") or ""),
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
