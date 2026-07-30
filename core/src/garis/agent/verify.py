"""Did it actually work?

GARIS reports "done" only after checking, because an agent that trusts its own
tool calls will confidently tell the user a service is running when the restart
silently failed. Cheap and deterministic first: if a step failed, the goal is not
met and no model call is needed. A model is consulted only to judge stated
criteria against real results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..errors import GarisError
from ..models import Job, Message, ModelRouter, Need, Privacy
from .goal import Goal

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
    checked_by: str = "rules"        # "rules" or "model"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "note": self.note,
            "unmet": list(self.unmet),
            "checked_by": self.checked_by,
        }


@dataclass(slots=True)
class StepEvidence:
    """What a finished step actually produced, trimmed for a prompt."""

    tool: str
    purpose: str
    ok: bool
    expects: str = ""
    summary: str = ""
    error: str = ""
    skipped: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


class Verifier:
    def __init__(self, router: ModelRouter, *, language: str = "pl") -> None:
        self.router = router
        self.language = language

    async def check(self, goal: Goal, evidence: list[StepEvidence]) -> Verification:
        required_failed = [e for e in evidence if not e.ok and not e.skipped]
        if required_failed:
            first = required_failed[0]
            return Verification(
                ok=False,
                note=f"Krok {first.tool} nie zakończył się poprawnie: {first.error[:200]}",
                unmet=tuple(f"{e.tool}: {e.error[:120]}" for e in required_failed[:3]),
            )

        if not evidence:
            return Verification(ok=False, note="Nie wykonano żadnego kroku.")

        if not goal.criteria and not any(e.expects for e in evidence):
            # Nothing was declared to check, and nothing failed. Do not spend a
            # model call inventing doubt.
            return Verification(ok=True, note="Wszystkie kroki wykonane bez błędów.")

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
            # The verifier failing is not the task failing; say so honestly.
            return Verification(
                ok=True,
                note="Kroki wykonane bez błędów, ale nie udało mi się dodatkowo "
                     "zweryfikować rezultatu.",
                checked_by="rules",
            )
        if not isinstance(data, dict):
            return Verification(ok=True, note="Kroki wykonane bez błędów.")
        return Verification(
            ok=bool(data.get("ok", False)),
            note=str(data.get("note") or ""),
            unmet=tuple(str(u) for u in (data.get("unmet") or [])),
            checked_by="model",
        )


__all__ = ["VERIFY_PROMPT", "StepEvidence", "Verification", "Verifier"]
