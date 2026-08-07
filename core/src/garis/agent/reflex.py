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

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..kernel.contracts import CapabilityTarget, StepTarget, ToolTarget
from ..text import fold
from .goal import Goal, Plan, PlanStep

ORIGIN = "reflex"


@dataclass(frozen=True, slots=True)
class Reflex:
    """One question this machine can answer about itself."""

    name: str
    #: What answers this question — a legacy tool today, a capability once one
    #: exists for the same measurement. Keyed on the target rather than on its
    #: name so that repointing a reflex cannot silently miss: an unknown *name*
    #: returns "I cannot check this", an unknown *target* is simply absent.
    target: StepTarget
    purpose: str
    #: Every group must contribute a word — "ile miejsca na dysku" needs both a
    #: quantity word and a disk word, or "wyślij plik na dysk" would match.
    needs: tuple[frozenset[str], ...]
    #: "" when the value is usable; otherwise why it is not.
    validate: Callable[[Any], str]
    #: The answer, built from measured values.
    present: Callable[[Any], str]
    params: dict[str, Any] = field(default_factory=dict)
    #: Parameters read out of the sentence itself, for the one case where a
    #: fixed set will not do: "czy Discord działa" names the program. Returning
    #: None means the phrase did not actually contain what this reflex needs, so
    #: there is no reflex after all — better than guessing a name.
    params_from: Callable[[str], dict[str, Any] | None] | None = None

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


#: How far the numbers may drift from each other before the set is incoherent.
#: A megabyte of slack on a real disk is rounding; on a nonsense reading of ten
#: bytes it would swallow the nonsense, so the allowance is also capped at a
#: thousandth of the whole.
TOLERANCE_BYTES = 1024 * 1024
TOLERANCE_PERCENT = 0.15


def _slack(total: float) -> float:
    return max(min(TOLERANCE_BYTES, total / 1000), 1.0)


def _validate_disk(value: Any) -> str:
    """Range checks *and* consistency checks.

    Checking `free <= total` was not enough: a container reported 252 GB total,
    24,1 GB free and 5% used, and every one of those passed while the set as a
    whole said two incompatible things. The percentage now has to follow from the
    same subtraction as the free space, and the parts have to add up to the
    whole.
    """
    if not isinstance(value, dict):
        return "narzędzie nie zwróciło danych o dysku"
    total = _number(value.get("total"))
    free = _number(value.get("free"))
    if total is None or free is None:
        return "brak zmierzonego rozmiaru dysku w wyniku"
    if total <= 0:
        return "zmierzony rozmiar dysku to zero"
    slack = _slack(total)
    if free > total + slack:
        return "wolne miejsce większe niż pojemność — wynik nie ma sensu"

    used = _number(value.get("used"))
    if used is not None and abs((total - free) - used) > slack:
        return "zajęte + wolne nie sumuje się do pojemności"

    percent = _number(value.get("percent_used"))
    if percent is not None:
        expected = (total - free) / total * 100
        if abs(percent - expected) > TOLERANCE_PERCENT:
            return (
                f"procent zajętości ({percent:.1f}%) nie zgadza się z wolnym "
                f"miejscem ({expected:.1f}%)"
            )

    reserved = _number(value.get("reserved"))
    filesystem_used = _number(value.get("filesystem_used"))
    if reserved is not None and filesystem_used is not None and used is not None:
        if abs(filesystem_used + reserved - used) > slack:
            return "zajęte przez pliki + zarezerwowane nie sumuje się do zajętych"
    return ""


def _present_disk(value: Any) -> str:
    """One sentence whose numbers agree with each other.

    The percentage is the same subtraction as the free space, and where a quota
    makes "free on the disk" differ from "free to you" the sentence says so
    instead of leaving the reader to reconcile two figures that cannot be
    reconciled from the outside.
    """
    assert isinstance(value, dict)
    where = str(value.get("path") or "").strip()
    total = _number(value.get("total")) or 0.0
    free = _number(value.get("free")) or 0.0
    place = f"Na {where}" if where else "Na dysku"
    percent = (total - free) / total * 100 if total else 0.0
    sentence = f"{place} zostało {_gib(free)} z {_gib(total)} — zajęte {percent:.0f}%."

    reserved = _number(value.get("reserved")) or 0.0
    # Only when it is big enough to be the thing that confuses the reader.
    if total and reserved / total > 0.01:
        files = _number(value.get("filesystem_used")) or 0.0
        sentence += (
            f" Pliki zajmują {_gib(files)}; pozostałe {_gib(reserved)} jest wolne"
            " na woluminie, ale niedostępne tutaj (limit systemu lub kontenera)."
        )
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
    slack = _slack(total)
    if available > total + slack:
        return "wolnej pamięci więcej niż całej — wynik nie ma sensu"
    used = _number(value.get("used"))
    if used is not None and abs((total - available) - used) > slack:
        return "zajęta + wolna pamięć nie sumuje się do całości"
    percent = _number(value.get("percent_used"))
    if percent is not None:
        expected = (total - available) / total * 100
        if abs(percent - expected) > TOLERANCE_PERCENT:
            return "procent zajętej pamięci nie zgadza się z wolną"
    return ""


def _present_memory(value: Any) -> str:
    assert isinstance(value, dict)
    total = _number(value.get("total")) or 0.0
    available = _number(value.get("available")) or 0.0
    percent = (total - available) / total * 100 if total else 0.0
    return f"Wolnej pamięci: {_gib(available)} z {_gib(total)} — zajęte {percent:.0f}%."


# ------------------------------------------------------------------ processes


def _validate_processes(value: Any) -> str:
    if not isinstance(value, dict):
        return "narzędzie nie zwróciło danych o procesach"
    scanned = _number(value.get("scanned"))
    if scanned is None:
        return "brak informacji, ile procesów przejrzano"
    # No machine has no processes. Zero means the reading failed, and a failed
    # reading must never be presented as "nothing is running".
    if scanned < 1:
        return "nie udało się odczytać listy procesów"
    return ""


def _present_processes(value: Any) -> str:
    assert isinstance(value, dict)
    scanned = int(_number(value.get("scanned")) or 0)
    rows = value.get("matched") or []
    query = str(value.get("query") or "").strip()

    if not query:
        sentence = f"Działa {scanned} {_processes_word(scanned)}."
        heaviest = max(
            (r for r in rows if isinstance(r, dict)),
            key=lambda r: _number(r.get("memory_mb")) or 0.0,
            default=None,
        )
        if heaviest:
            memory = _number(heaviest.get("memory_mb")) or 0.0
            name = str(heaviest.get("name") or "?")
            sentence += f" Najwięcej pamięci zajmuje {name}"
            sentence += f" — {memory:.0f} MB." if memory >= 1 else "."
        return sentence

    count = int(_number(value.get("match_count")) or 0)
    if not count:
        return (
            f"Nie widzę procesu „{query}”. Przejrzałem {scanned} "
            f"{_processes_word(scanned)}."
        )
    pids = ", ".join(str(int(_number(r.get("pid")) or 0)) for r in rows[:3]
                     if isinstance(r, dict))
    plural = "proces" if count == 1 else ("procesy" if 2 <= count <= 4 else "procesów")
    return f"Tak, „{query}” działa — {count} {plural} (PID: {pids})."


def _processes_word(count: int) -> str:
    if count == 1:
        return "proces"
    if 2 <= count % 10 <= 4 and count % 100 not in range(12, 15):
        return "procesy"
    return "procesów"


def _named_process(text: str) -> dict[str, Any] | None:
    """The program someone asked about, taken from their own words.

    Only the shapes that actually name something — "czy X działa", "czy X jest
    uruchomiony". A sentence that names nothing gets no reflex, because the
    alternative is inventing a process name and then reporting on it.
    """
    words = fold(text).split()
    for marker in ("czy", "is"):
        if marker in words:
            after = words[words.index(marker) + 1:]
            name = [w for w in after if w not in _RUNNING and w not in _NOISE]
            if name:
                return {"name": name[0]}
    return None


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
_PROCESSES = frozenset({"procesy", "proces", "procesow", "programy", "program",
                        "aplikacje", "processes", "apps", "tasks"})
_SHOW = frozenset({"pokaz", "wypisz", "lista", "liste", "wylistuj", "jakie",
                   "uruchomione", "dzialajace", "show", "list", "running"})
_RUNNING = frozenset({"dziala", "uruchomiony", "uruchomiona", "uruchomione",
                      "wlaczony", "wlaczona", "running", "open", "otwarty"})
#: Words that are not a program name, however grammatical they look. "czy to
#: jest uruchomione" names nothing, and `to` must not become a process query.
_NOISE = frozenset({"jest", "sa", "teraz", "jeszcze", "czy", "is", "the", "u",
                    "mnie", "na", "komputerze", "moim", "to", "tam", "cos",
                    "jakis", "gdzies", "it", "that", "this", "still", "already"})
_IS_IT = frozenset({"czy", "is"})
_NOW = frozenset({"teraz", "jest", "obecnie", "now", "current", "aktualnie"})

_VOLUME = frozenset({"glosnosc", "glosnosci", "glosno", "volume", "dzwiek", "dzwieku",
                     "wyciszony", "wyciszenie", "wyciszone", "muted", "mute"})
#: "jaka jest głośność" / "ile mam głośności" / "czy komputer jest wyciszony" all
#: carry one of these; "podgłośnij" carries none, and must reach the planner.
_ASKING = frozenset({"jaka", "jaki", "jakie", "ile", "czy", "jest", "mam", "teraz",
                     "sprawdz", "pokaz", "obecnie"})


def _validate_volume(value: Any) -> str:
    """The same arithmetic the capability's verifier applies, asked again here.

    A reflex plan earns "sprawdzone" without a model, so it re-derives the
    percentage rather than trusting the one it was handed.
    """
    if not isinstance(value, dict):
        return "narzedzie nie zwrocilo danych o glosnosci"
    scalar = value.get("volume_scalar")
    percent = value.get("volume_percent")
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
        return "brak zmierzonego poziomu glosnosci"
    if not math.isfinite(float(scalar)) or not 0.0 <= float(scalar) <= 1.0:
        return "zmierzony poziom glosnosci jest poza zakresem"
    if isinstance(percent, bool) or not isinstance(percent, int):
        return "brak poziomu glosnosci w procentach"
    if percent != math.floor(float(scalar) * 100 + 0.5):
        return "procent nie wynika ze zmierzonego poziomu"
    if not isinstance(value.get("muted"), bool):
        return "brak informacji o wyciszeniu"
    if not str(value.get("endpoint_id") or "").strip():
        return "odczyt nie mowi, ktorego urzadzenia dotyczy"
    return ""


def _present_volume(value: Any) -> str:
    percent = value["volume_percent"]
    if value["muted"]:
        return f"Głośność jest ustawiona na {percent}%, ale dźwięk jest wyciszony."
    return f"Głośność: {percent}%."

REFLEXES: tuple[Reflex, ...] = (
    Reflex(
        name="disk-space",
        target=ToolTarget("disk_usage"),
        purpose="Odczyt wolnego miejsca na dysku",
        needs=(_DISK, _QUANTITY),
        validate=_validate_disk,
        present=_present_disk,
    ),
    Reflex(
        name="memory",
        target=ToolTarget("memory_usage"),
        purpose="Odczyt zajętości pamięci",
        needs=(_MEMORY, _QUANTITY),
        validate=_validate_memory,
        present=_present_memory,
    ),
    Reflex(
        # Before the general one: "czy Discord działa" also contains "dziala",
        # and the two must not race.
        name="process-named",
        target=ToolTarget("process_find"),
        purpose="Sprawdzenie, czy dany program działa",
        needs=(_IS_IT, _RUNNING),
        validate=_validate_processes,
        present=_present_processes,
        params_from=_named_process,
    ),
    Reflex(
        name="process-list",
        target=ToolTarget("process_find"),
        purpose="Odczyt listy działających procesów",
        needs=(_PROCESSES, _SHOW),
        validate=_validate_processes,
        present=_present_processes,
    ),
    Reflex(
        name="system-info",
        target=ToolTarget("system_info"),
        purpose="Odczyt informacji o systemie",
        needs=(_SYSTEM, _WHAT),
        validate=_validate_system,
        present=_present_system,
    ),
    Reflex(
        # The first reflex that answers with a capability rather than a tool.
        # Keying the table on `StepTarget` is what makes that a one-line change.
        name="audio-volume",
        target=CapabilityTarget("windows.audio.master.get"),
        purpose="Odczyt głośności i wyciszenia",
        needs=(_VOLUME, _ASKING),
        validate=_validate_volume,
        present=_present_volume,
    ),
    Reflex(
        name="clock",
        target=ToolTarget("current_time"),
        purpose="Odczyt czasu lokalnego",
        needs=(_TIME, _NOW),
        validate=_validate_time,
        present=_present_time,
    ),
)

BY_TARGET: dict[StepTarget, Reflex] = {r.target: r for r in REFLEXES}


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


def plan_for(
    goal: Goal | str, *, available: Callable[[StepTarget], bool] | None = None
) -> Plan | None:
    """A one-step plan for a question this machine can answer about itself.

    `available` decides whether the target exists and runs here — passed in
    rather than imported, because the agent layer must not reach into a registry
    to ask a question the caller already knows the answer to.
    """
    text = goal.text if isinstance(goal, Goal) else goal
    reflex = find(goal)
    if reflex is None:
        return None
    if available is not None and not available(reflex.target):
        return None

    params = dict(reflex.params)
    if reflex.params_from is not None:
        derived = reflex.params_from(text)
        if derived is None:
            # The phrase matched the shape but named nothing this reflex can act
            # on. Hand it to the planner rather than guessing a parameter.
            return None
        params.update(derived)

    return Plan(
        summary=reflex.purpose,
        steps=[
            PlanStep(
                key=reflex.name,
                target=reflex.target,
                params=params,
                purpose=reflex.purpose,
                expects="zmierzone wartości",
            )
        ],
        origin=ORIGIN,
    )


def check(target: StepTarget, value: Any) -> str:
    """"" when the step's output is a usable measurement, else why it is not."""
    reflex = BY_TARGET.get(target)
    if reflex is None:
        return f"nie umiem sprawdzić wyniku kroku {target.name}"
    return reflex.validate(value)


def answer(target: StepTarget, value: Any) -> str:
    """The sentence to show, built from the measured values. Empty if unusable."""
    reflex = BY_TARGET.get(target)
    if reflex is None or reflex.validate(value):
        return ""
    return reflex.present(value)


__all__ = ["BY_TARGET", "ORIGIN", "REFLEXES", "Reflex", "answer", "check", "find", "plan_for"]
