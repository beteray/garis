"""What GARIS says when it is done.

Built from facts, not from a model. A generated summary would be a second place
where the truth could drift from what happened; the audit trail and the step
results already know. Persona shapes the wording afterwards and cannot change the
content.

Three registers:
  * ``short``    — one to three sentences. The default.
  * ``details``  — per-step account, shown in verbose mode.
  * ``developer``— tools, parameters, timings, plan JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import reflex
from .goal import Goal, Plan
from .verify import StepEvidence, Verification


@dataclass(slots=True)
class Report:
    short: str
    details: str = ""
    developer: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    problems: tuple[str, ...] = ()
    #: The measured result in one sentence, when the work produced one — built
    #: from the tool's own numbers. Empty when nothing measurable came back,
    #: which is itself the honest answer.
    answer: str = ""

    def render(self, *, verbose: bool = False) -> str:
        return f"{self.short}\n\n{self.details}".strip() if verbose and self.details \
            else self.short

    def to_dict(self) -> dict[str, Any]:
        return {
            "short": self.short,
            "details": self.details,
            "verified": self.verified,
            "problems": list(self.problems),
            "developer": self.developer,
            "answer": self.answer,
        }


def build_report(
    goal: Goal,
    plan: Plan,
    evidence: list[StepEvidence],
    verification: Verification,
    *,
    duration_seconds: float = 0.0,
    repairs: int = 0,
) -> Report:
    done = [e for e in evidence if e.ok and not e.skipped]
    failed = [e for e in evidence if not e.ok and not e.skipped]
    skipped = [e for e in evidence if e.skipped]

    problems: list[str] = []
    problems += [f"{e.name}: {e.error[:160]}" for e in failed]
    if verification.unmet:
        problems += list(verification.unmet)

    # The answer, when the work produced one. A person who asked how much disk
    # is left wants the number, not their own sentence read back with "gotowe"
    # in front of it — which is precisely what this used to say, while the
    # measured bytes sat unused two fields away in `details`.
    measured = _measured_answer(done)

    headline = measured or _headline(goal, verification, done, failed)
    pieces = [headline]

    if not measured:
        if verification.ok and verification.checked:
            pieces.append(verification.note or "Sprawdziłem rezultat.")
        elif verification.ok:
            # Nothing checked the outcome. Say that, in place of the note that
            # used to imply the opposite.
            pieces.append(verification.note or "Nie sprawdzałem rezultatu.")
        elif verification.note:
            pieces.append(verification.note)

    if repairs:
        pieces.append(
            f"Po drodze zmieniałem metodę {repairs} raz{'y' if repairs > 1 else ''}."
        )
    if skipped:
        pieces.append(f"Pominąłem {len(skipped)} krok(i) opcjonalny(ch).")

    short = " ".join(p.strip() for p in pieces if p and p.strip())

    detail_lines: list[str] = []
    if plan.summary:
        detail_lines.append(f"Plan: {plan.summary}")
    if plan.assumptions:
        detail_lines.append("Założenia: " + "; ".join(plan.assumptions))
    for index, item in enumerate(evidence, start=1):
        mark = "—" if item.skipped else ("✓" if item.ok else "✗")
        line = f"{mark} {index}. {item.name}"
        if item.purpose:
            line += f" — {item.purpose}"
        if item.ok and item.summary:
            line += f"\n     {item.summary[:300]}"
        elif item.error:
            line += f"\n     błąd: {item.error[:300]}"
        detail_lines.append(line)
    if duration_seconds:
        detail_lines.append(f"Czas: {_duration(duration_seconds)}")

    return Report(
        short=short,
        answer=measured,
        details="\n".join(detail_lines),
        developer={
            "plan": plan.to_dict(),
            "steps": [
                {
                    # The window and the audit view read this key. It stays a
                    # string for both kinds of step; `capability` names which.
                    "tool": e.name,
                    "ok": e.ok,
                    "skipped": e.skipped,
                    "error": e.error,
                    "extras": e.extras,
                }
                for e in evidence
            ],
            "verification": verification.to_dict(),
            "duration_seconds": round(duration_seconds, 2),
            "repairs": repairs,
        },
        # "Verified" is a claim about a check that ran, not about the absence of
        # errors, and an uncertain effect is not a verified one. All three facts
        # come from the verifier, and `Verification.verified` is where they meet.
        verified=verification.verified,
        problems=tuple(problems),
    )


def blocked_report(goal: Goal, question: str) -> Report:
    return Report(
        short=question,
        details=f"Cel: {goal.text}",
        verified=False,
        problems=("brak informacji od użytkownika",),
    )


def failed_report(goal: Goal, reason: str, *, details: str = "") -> Report:
    return Report(
        short=reason,
        details=details or f"Cel: {goal.text}",
        verified=False,
        problems=(reason,),
    )


def _headline(
    goal: Goal,
    verification: Verification,
    done: list[StepEvidence],
    failed: list[StepEvidence],
) -> str:
    subject = _trim(goal.text)
    if verification.ok:
        return f"Gotowe: {subject}."
    if failed:
        return f"Nie udało się dokończyć: {subject}."
    if not done:
        return f"Nie wykonałem nic dla: {subject}."
    return f"Częściowo zrobione: {subject}."


def _measured_answer(done: list[StepEvidence]) -> str:
    """One sentence built from what a tool actually returned, or nothing.

    Only for results this codebase knows how to read — the reflex table. Guessing
    a sentence out of an arbitrary tool's output would be the same invention this
    file exists to avoid; for everything else the caller falls back to naming the
    goal, which at least does not claim more than it knows.
    """
    for item in done:
        sentence = reflex.answer(item.tool, item.value)
        if sentence:
            return sentence
    return ""


def _trim(text: str, limit: int = 120) -> str:
    flat = " ".join(text.split())
    trimmed = flat if len(flat) <= limit else flat[: limit - 1] + "…"
    return trimmed.rstrip(".")


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


__all__ = ["Report", "blocked_report", "build_report", "failed_report"]
