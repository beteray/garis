"""Questions this machine can answer about itself, without a model.

"How much disk is left?" needs a `shutil.disk_usage` call, not a language model.
Until now it went to the planner anyway, and on a machine with no API key the
planner was a stub provider that guessed a tool from keyword overlap and then —
asked to verify its own work — replied `{"ok": true, "note": "Sprawdzone."}` to
anything at all. Real numbers were read off the disk and then thrown away, and
the user was shown their own sentence back with a verification that never
happened.

So this module owns three things for a small, fixed set of local questions, and
each of them is a fact rather than a judgement:

  * **which tool answers it** — a table, not a guess;
  * **whether the result is usable** — the structured output is parsed and
    range-checked, which is what makes "sprawdzone" mean something;
  * **what to say** — a sentence built from the measured values.

Nothing here can invent a result: every presenter reads numbers out of the tool's
own return value, and every validator refuses output it cannot parse. A reflex
that cannot match, cannot validate or cannot present is no reflex at all, and the
request goes to the planner — which, with no model configured, blocks honestly.

Deliberately narrow. These are read-only questions with no parameters to guess.
Anything that changes the machine needs a plan, and a plan needs a model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..text import fold
from .goal import Goal, Plan, PlanStep

ORIGIN = "reflex"


@dataclass(frozen=True, slots=True)
class Reflex:
    """One question this machine can answer about itself."""

    name: str
    tool: str
    purpose: str
    #: Every group must contribute a word — "ile miejsca na dysku" needs both a
    #: quantity word and a disk word, or "wyślij plik na dysk" would match.
    needs: tuple[frozenset[str], ...]
    #: "" when the value is usable; otherwise why it is not.
    validate: Callable[[Any], str]
    #: The answer, built from measured values.
    present: Callable[[Any], str]
    params: dict[str, Any] = field(default_factory=dict)

    def matches(self, words: set[str]) -> bool:
        return all(group & words for group in self.needs)


# ------------------------------------------------------------------ formatting


def _gib(value: float) -> str:
    """Bytes as gigabytes, in Polish, with a comma. 84.2 GB, not 84.19999GB."""
    return f"{value / 1024 ** 3:.1f}".replace(".", ",") + " GB"


def _number(value: Any) -> float | None:
    """A finite, non-negative number, or nothing. Strings included: a tool that
    returns "270553174016" is still telling the truth, but `None` and `"?"` are
    not numbers and must never be formatted as one."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


# ----------------------------------------------------------------- disk space


def _validate_disk(value: Any) -> str:
    if not isinstance(value, dict):
        return "narzędzie nie zwróciło danych o dysku"
    total = _number(value.get("total"))
    free = _number(value.get("free"))
    if total is None or free is None:
        return "brak zmierzonego rozmiaru dysku w wyniku"
    if total <= 0:
        return "zmierzony rozmiar dysku to zero"
    if free > total:
        return "wolne miejsce większe niż pojemność — wynik nie ma sensu"
    return ""


def _present_disk(value: Any) -> str:
    assert isinstance(value, dict)
    where = str(value.get("path") or "").strip()
    total = _number(value.get("total")) or 0.0
    free = _number(value.get("free")) or 0.0
    used_percent = _number(value.get("percent_used"))
    place = f"Na {where}" if where else "Na dysku"
    sentence = f"{place} zostało {_gib(free)} z {_gib(total)}."
    if used_percent is not None:
        percent = f"{used_percent:.0f}".replace(".", ",")
        sentence += f" Zajęte: {percent}%."
    return sentence


# --------------------------------------------------------------------- memory


def _validate_memory(value: Any) -> str:
    if not isinstance(value, dict):
        return "narzędzie nie zwróciło danych o pamięci"
    total = _number(value.get("total"))
    available = _number(value.get("available"))
    if total is None or available is None:
        return "brak zmierzonej pamięci w wyniku"
    if total <= 0:
        return "zmierzona pamięć to zero"
    return ""


def _present_memory(value: Any) -> str:
    assert isinstance(value, dict)
    total = _number(value.get("total")) or 0.0
    available = _number(value.get("available")) or 0.0
    return f"Wolnej pamięci: {_gib(available)} z {_gib(total)}."


# ------------------------------------------------------------------ the system


def _validate_system(value: Any) -> str:
    if not isinstance(value, dict):
        return "narzędzie nie zwróciło danych o systemie"
    if not str(value.get("system") or "").strip():
        return "brak nazwy systemu w wyniku"
    return ""


def _present_system(value: Any) -> str:
    assert isinstance(value, dict)
    parts = [str(value.get("system") or "").strip()]
    release = str(value.get("release") or "").strip()
    if release:
        parts.append(release)
    machine = str(value.get("machine") or "").strip()
    line = " ".join(p for p in parts if p)
    sentence = f"System: {line}."
    if machine:
        sentence += f" Architektura: {machine}."
    host = str(value.get("hostname") or "").strip()
    if host:
        sentence += f" Nazwa komputera: {host}."
    return sentence


# ---------------------------------------------------------------------- clock


def _validate_time(value: Any) -> str:
    if isinstance(value, dict) and str(value.get("local") or "").strip():
        return ""
    if isinstance(value, str) and value.strip():
        return ""
    return "narzędzie nie zwróciło czasu"


def _present_time(value: Any) -> str:
    if isinstance(value, dict):
        local = str(value.get("local") or "").strip()
        zone = str(value.get("timezone") or "").strip()
        return f"Jest {local}." + (f" Strefa: {zone}." if zone else "")
    return f"Jest {str(value).strip()}."


# ------------------------------------------------------------------ the table

_QUANTITY = frozenset({"ile", "wolne", "wolnego", "zostalo", "miejsca", "miejsce",
                       "zajete", "zajetosc", "space", "free", "left", "usage"})
_DISK = frozenset({"dysk", "dysku", "dysku;", "dysko", "dyski", "disk", "drive",
                   "partycja", "partycji", "ssd"})
_MEMORY = frozenset({"pamiec", "pamieci", "ram", "memory"})
_SYSTEM = frozenset({"system", "systemu", "windows", "os", "wersja", "wersje",
                     "wersji", "komputer", "komputerze", "maszyna"})
_WHAT = frozenset({"jaki", "jaka", "jakie", "ktory", "ktora", "co", "what",
                   "informacje", "info", "which", "version"})
_TIME = frozenset({"godzina", "godzine", "czas", "time", "clock", "zegar"})
_NOW = frozenset({"teraz", "jest", "obecnie", "now", "current", "aktualnie"})

REFLEXES: tuple[Reflex, ...] = (
    Reflex(
        name="disk-space",
        tool="disk_usage",
        purpose="Odczyt wolnego miejsca na dysku",
        needs=(_DISK, _QUANTITY),
        validate=_validate_disk,
        present=_present_disk,
    ),
    Reflex(
        name="memory",
        tool="memory_usage",
        purpose="Odczyt zajętości pamięci",
        needs=(_MEMORY, _QUANTITY),
        validate=_validate_memory,
        present=_present_memory,
    ),
    Reflex(
        name="system-info",
        tool="system_info",
        purpose="Odczyt informacji o systemie",
        needs=(_SYSTEM, _WHAT),
        validate=_validate_system,
        present=_present_system,
    ),
    Reflex(
        name="clock",
        tool="current_time",
        purpose="Odczyt czasu lokalnego",
        needs=(_TIME, _NOW),
        validate=_validate_time,
        present=_present_time,
    ),
)

BY_TOOL: dict[str, Reflex] = {r.tool: r for r in REFLEXES}


def _words(text: str) -> set[str]:
    return set(fold(text).split())


def find(goal: Goal | str) -> Reflex | None:
    """The reflex that answers this goal, if one does.

    First match wins, and the table is ordered from most specific to least. A
    goal that mentions two subjects ("ile pamięci i miejsca na dysku") gets the
    first — a second step would need a plan, and a plan needs a model.
    """
    text = goal.text if isinstance(goal, Goal) else goal
    words = _words(text)
    for reflex in REFLEXES:
        if reflex.matches(words):
            return reflex
    return None


def plan_for(goal: Goal | str, *, available: Callable[[str], bool] | None = None) -> Plan | None:
    """A one-step plan for a question this machine can answer about itself.

    `available` decides whether the tool exists and runs here — passed in rather
    than imported, because the agent layer must not reach into the registry to
    ask a question the caller already knows the answer to.
    """
    reflex = find(goal)
    if reflex is None:
        return None
    if available is not None and not available(reflex.tool):
        return None
    return Plan(
        summary=reflex.purpose,
        steps=[
            PlanStep(
                key=reflex.name,
                tool=reflex.tool,
                params=dict(reflex.params),
                purpose=reflex.purpose,
                expects="zmierzone wartości",
            )
        ],
        origin=ORIGIN,
    )


def check(tool: str, value: Any) -> str:
    """"" when the tool's output is a usable measurement, else why it is not."""
    reflex = BY_TOOL.get(tool)
    if reflex is None:
        return f"nie umiem sprawdzić wyniku narzędzia {tool}"
    return reflex.validate(value)


def answer(tool: str, value: Any) -> str:
    """The sentence to show, built from the measured values. Empty if unusable."""
    reflex = BY_TOOL.get(tool)
    if reflex is None or reflex.validate(value):
        return ""
    return reflex.present(value)


__all__ = ["BY_TOOL", "ORIGIN", "REFLEXES", "Reflex", "answer", "check", "find", "plan_for"]
