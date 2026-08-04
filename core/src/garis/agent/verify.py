"""Did it actually work?

GARIS reports "done" only after checking, because an agent that trusts its own
tool calls will confidently tell the user a service is running when the restart
silently failed.

Two questions, deliberately kept apart, because collapsing them is how this file
came to certify work nobody had looked at:

  * **ok** — as far as anything here can tell, is the goal met?
  * **checked** — did a verifier actually run against real evidence?

"No step reported an error" answers the first and not the second. A task where
nothing failed but nothing was inspected is *finished, unchecked* — and it must
say so, rather than borrow the word "sprawdzone" from a check that never
happened. Before this split, a missing model produced `ok=True` with a note
apologising for not verifying, and the report printed `verified: true` anyway.

What counts as checking, in order of preference:

  1. **A structured postcondition.** For reflex plans (agent/reflex.py) the
     tool's own return value is parsed and range-checked. This is the strongest
     kind: it is arithmetic on measured bytes, not an opinion.
  2. **A model judging stated criteria** against real step results — and only a
     model that is a real provider. A stub that answers `{"ok": true}` to every
     prompt is not a verifier, and this module now refuses its verdict.
  3. **Nothing.** Which is a legitimate outcome, reported as such.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..errors import GarisError
from ..models import Job, Message, ModelRouter, Need, Privacy
from . import reflex
from .goal import Goal, Plan

VERIFY_PROMPT = """\
Oceń, czy cel został osiągnięty, na podstawie rzeczywistych wyników kroków.

Bądź surowy: „polecenie się wykonało” nie znaczy „cel osiągnięty”. Jeśli wyniki
nie potwierdzają rezultatu, uznaj cel za nieosiągnięty i napisz, czego brakuje.

Odpowiadaj wyłącznie JSON-em (nawiasy podwójne poniżej to escape dla str.format):
{{"ok": true/false, "note": "jedno zdanie", "unmet": ["niespełnione kryteria"]}}
Odpowiadaj w języku: {language}.
"""


@dataclass(slots=True)
class Verification:
    ok: bool
    note: str = ""
    unmet: tuple[str, ...] = ()
    #: "rules" — a deterministic postcondition ran; "model" — a real provider
    #: judged the evidence; "none" — nothing checked anything.
    checked_by: str = "none"

    @property
    def checked(self) -> bool:
        """Did a verifier actually run? This, not `ok`, licenses the word
        "sprawdzone" anywhere in the interface."""
        return self.checked_by in ("rules", "model")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "note": self.note,
            "unmet": list(self.unmet),
            "checked_by": self.checked_by,
            "checked": self.checked,
        }


@dataclass(slots=True)
class StepEvidence:
    """What a finished step actually produced.

    `value` is the tool's own return value, kept structured. `summary` is that
    value flattened for a prompt — useful to a model, useless to a validator,
    and the reason a postcondition check could not be written before.
    """

    tool: str
    purpose: str
    ok: bool
    expects: str = ""
    summary: str = ""
    error: str = ""
    skipped: bool = False
    value: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


class Verifier:
    def __init__(self, router: ModelRouter, *, language: str = "pl") -> None:
        self.router = router
        self.language = language

    async def check(
        self,
        goal: Goal,
        evidence: list[StepEvidence],
        *,
        plan: Plan | None = None,
    ) -> Verification:
        required_failed = [e for e in evidence if not e.ok and not e.skipped]
        if required_failed:
            first = required_failed[0]
            return Verification(
                ok=False,
                note=f"Krok {first.tool} nie zakończył się poprawnie: {first.error[:200]}",
                unmet=tuple(f"{e.tool}: {e.error[:120]}" for e in required_failed[:3]),
                checked_by="rules",
            )

        ran = [e for e in evidence if e.ok and not e.skipped]
        if not ran:
            return Verification(
                ok=False,
                note="Nie wykonano żadnego kroku.",
                checked_by="rules",
            )

        # A reflex plan carries its own postcondition: the tool's structured
        # output is parsed and range-checked. This is the only path in GARIS that
        # earns "sprawdzone" without a model, and it earns it on arithmetic.
        if plan is not None and plan.origin == reflex.ORIGIN:
            return self._check_reflex(ran)

        if not goal.criteria and not any(e.expects for e in evidence):
            # Nothing was declared to check, and nothing failed. Do not spend a
            # model call inventing doubt — but do not call this verified either.
            return Verification(
                ok=True,
                note="Wszystkie kroki wykonane bez błędów. Nie sprawdzałem rezultatu.",
                checked_by="none",
            )

        payload = {
            "goal": goal.text,
            "criteria": list(goal.criteria),
            "steps": [
                {
                    "tool": e.tool,
                    "purpose": e.purpose,
                    "expected": e.expects,
                    "result": e.summary[:1500],
                    "skipped": e.skipped,
                }
                for e in evidence
            ],
        }
        try:
            completion = await self.router.complete(
                Need(job=Job.REASON,
                     privacy=Privacy.PREFER_LOCAL if goal.private else Privacy.ANY),
                [
                    Message.system(VERIFY_PROMPT.format(language=self.language)),
                    Message.user(json.dumps(payload, ensure_ascii=False)),
                ],
                json_mode=True,
                temperature=0.0,
                max_tokens=600,
            )
            data = completion.json()
        except (GarisError, ValueError):
            # The verifier failing is not the task failing; say so honestly, and
            # do not claim the check happened.
            return Verification(
                ok=True,
                note="Kroki wykonane bez błędów, ale nie udało mi się sprawdzić rezultatu.",
                checked_by="none",
            )

        if completion.stub:
            # A provider that answers every prompt with a canned reply is not a
            # verifier. Taking its "ok" was how a task nobody inspected came out
            # marked "Zrobione i sprawdzone".
            return Verification(
                ok=True,
                note="Kroki wykonane bez błędów. Rezultatu nie sprawdzał żaden model.",
                checked_by="none",
            )
        if not isinstance(data, dict):
            return Verification(
                ok=True,
                note="Kroki wykonane bez błędów. Odpowiedź weryfikatora była nieczytelna.",
                checked_by="none",
            )
        return Verification(
            ok=bool(data.get("ok", False)),
            note=str(data.get("note") or ""),
            unmet=tuple(str(u) for u in (data.get("unmet") or [])),
            checked_by="model",
        )

    @staticmethod
    def _check_reflex(ran: list[StepEvidence]) -> Verification:
        problems = [
            f"{e.tool}: {why}"
            for e in ran
            if (why := reflex.check(e.tool, e.value))
        ]
        if problems:
            return Verification(
                ok=False,
                note=f"Narzędzie zwróciło wynik, którego nie da się użyć: {problems[0]}",
                unmet=tuple(problems),
                checked_by="rules",
            )
        return Verification(
            ok=True,
            note="Sprawdziłem zmierzone wartości.",
            checked_by="rules",
        )


__all__ = ["VERIFY_PROMPT", "StepEvidence", "Verification", "Verifier"]
