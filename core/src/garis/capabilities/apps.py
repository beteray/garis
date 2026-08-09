"""Starting a program, and knowing afterwards whether it is running.

The capability the whole effect machinery was built for. `app_launch` the tool
returns `{"target": "discord", "pid": 8112}` the moment `CreateProcess` succeeds,
and that is exactly the shape of claim this codebase exists to refuse: a pid is
proof that something was spawned, not that Discord is up.

So this one is built the other way round.

**"It is running" means a list said so.** The executor reads the process table
after launching and keeps reading until the named process appears or the deadline
passes. The verifier is handed that reading. Nothing anywhere concludes from the
launcher having returned.

**A launcher's pid is usually the wrong pid.** Discord, Steam and every browser
start a stub that hands off to an existing instance and exits; watching the
spawned pid would report failure while the program is on screen. The measurement
is therefore by process *name*, and the spawned pid is recorded as a detail
rather than used as the test.

**Already running is not a reason to launch.** The table is read first. If the
program is there, nothing is started, the effect is `NOT_APPLIED`, and the answer
is that it was already open — which is both the truthful report and the thing
that stops "otwórz Discorda" from opening a second window every time a task is
resumed.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import ExecutionError
from ..kernel.contracts import (
    CapabilityTarget,
    Effect,
    EffectDisposition,
    Verification,
)
from ..kernel.effects import EffectRecord
from ..kernel.recovery import (
    Observation,
    RecoveryAssessment,
    RecoveryInspection,
    RecoveryStatus,
    evidence_ids_of,
)
from ..tools.system import _processes
from .base import Capability, Field, Invocation, Outcome, Permission, Risk, Verdict
from .processes import LIST as PROCESS_LIST
from .registry import REGISTRY

LAUNCH_ID = "windows.app.launch"

#: How long to keep looking for the process before giving up on it. A cold start
#: of a large application is seconds, not milliseconds, and answering "nie widzę
#: go" after 200 ms would be measuring the disk rather than the program.
APPEAR_TIMEOUT = 12.0
APPEAR_INTERVAL = 0.4


def process_name_for(target: str) -> str:
    """What to look for in the process table, given what was asked to start.

    `C:\\Program Files\\Discord\\Discord.exe` and `discord` both look for
    `discord`. Deliberately crude: the caller may state `process_name` outright,
    and this is only the guess for when nobody did.
    """
    name = str(target or "").strip().replace("\\", "/").rstrip("/")
    name = name.rsplit("/", 1)[-1]
    for suffix in (".exe", ".lnk", ".app", ".bat", ".cmd"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.strip().lower()


def running(rows: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    """Every row whose process name contains this one. The only way to ask."""
    needle = name.strip().lower()
    if not needle:
        return []
    return [r for r in rows if needle in str(r.get("name", "")).lower()]


@dataclass(frozen=True, slots=True)
class LaunchObservation:
    """What the machine looked like before and after, plus what was spawned."""

    was_running: bool
    is_running: bool
    matches: tuple[Mapping[str, Any], ...]
    scanned: int
    spawned_pid: int = 0
    waited_ms: int = 0


# ------------------------------------------------------------------- launching


async def _spawn(target: str, arguments: Sequence[str]) -> int:
    """Start it, and say nothing about what that means.

    Resolution mirrors the legacy `app_launch` tool, because "which binary does
    the word discord mean" is a question with one right answer per platform and
    two implementations of it would eventually disagree.
    """
    args = [str(a) for a in arguments]
    if sys.platform == "win32":
        argv = [shutil.which(target) or target, *args]
    elif sys.platform == "darwin":  # pragma: no cover - not a target platform
        argv = ["open", target, *args]
    else:
        resolved = shutil.which(target)
        argv = [resolved, *args] if resolved else ["xdg-open", target, *args]

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError) as exc:
        # Nothing was started: the binary is missing or the arguments are not
        # something the OS would accept. This is the one launch failure that is
        # safe to try again, and it is safe because nothing happened.
        raise ExecutionError(
            f"Nie udało się uruchomić {target!r}: {exc}", retryable=False
        ) from exc
    return int(process.pid)


async def _await_process(name: str, deadline: float) -> tuple[list[Mapping[str, Any]], int]:
    """Read the table until the process shows up or the clock runs out."""
    while True:
        rows = await asyncio.to_thread(_processes)
        found = running(rows, name)
        if found or time.monotonic() >= deadline:
            return found, len(rows)
        await asyncio.sleep(APPEAR_INTERVAL)


async def _launch(invocation: Invocation) -> Outcome:
    target = str(invocation.get("target", "") or "").strip()
    if not target:
        return Outcome(ok=False, error="Nie podano, co uruchomić.",
                       disposition=EffectDisposition.NOT_APPLIED)

    name = str(invocation.get("process_name", "") or "").strip() or process_name_for(target)
    arguments = invocation.get("arguments") or []

    before = await asyncio.to_thread(_processes)
    if not before:
        # No machine has no processes. An empty table is a failed reading, and
        # launching on the strength of it would mean starting a second copy of
        # something because the check broke.
        return Outcome(
            ok=False,
            error="Nie udało się odczytać listy procesów, więc nie ryzykuję uruchomienia.",
            evidence={"platform": sys.platform, "scanned": 0},
            disposition=EffectDisposition.NOT_APPLIED,
        )

    already = running(before, name)
    if already:
        return _outcome(
            target, name,
            LaunchObservation(
                was_running=True, is_running=True, matches=tuple(already),
                scanned=len(before),
            ),
            # Nothing was started, so nothing outside GARIS moved. This is what
            # keeps a resumed task from opening a second window.
            disposition=EffectDisposition.NOT_APPLIED,
        )

    started = time.monotonic()
    try:
        pid = await _spawn(target, arguments)
    except ExecutionError as exc:
        return Outcome(
            ok=False, error=str(exc),
            evidence={"platform": sys.platform, "process_name": name},
            disposition=EffectDisposition.NOT_APPLIED,
        )

    found, scanned = await _await_process(name, started + APPEAR_TIMEOUT)
    return _outcome(
        target, name,
        LaunchObservation(
            was_running=False, is_running=bool(found), matches=tuple(found),
            scanned=scanned, spawned_pid=pid,
            waited_ms=int((time.monotonic() - started) * 1000),
        ),
        # A process was created. Whether it is the one that was wanted is the
        # verifier's question; that something was started is not in doubt.
        disposition=EffectDisposition.APPLIED,
    )


def _outcome(
    target: str, name: str, seen: LaunchObservation, *, disposition: EffectDisposition
) -> Outcome:
    return Outcome(
        ok=True,
        value={
            "target": target,
            "process_name": name,
            "running": seen.is_running,
            "already_running": seen.was_running,
            "pids": [int(r.get("pid", 0) or 0) for r in seen.matches],
            "spawned_pid": seen.spawned_pid,
            "waited_ms": seen.waited_ms,
            "scanned": seen.scanned,
        },
        evidence={
            "platform": sys.platform,
            "process_name": name,
            "running": seen.is_running,
            "already_running": seen.was_running,
            "match_count": len(seen.matches),
            "scanned": seen.scanned,
        },
        disposition=disposition,
    )


# ---------------------------------------------------------------- verification


async def verify_launch(invocation: Invocation, outcome: Outcome) -> Verdict:
    """Is the program running? Read off a process table, or not answered at all."""
    if not outcome.ok:
        return Verdict(ok=False, checked=True,
                       note=outcome.error or "Nie udało się uruchomić programu.")

    value = outcome.value
    if not isinstance(value, dict):
        return Verdict(ok=False, checked=True, note="Uruchomienie nie zwróciło danych.")

    scanned = value.get("scanned")
    if not isinstance(scanned, int) or scanned <= 0:
        # Without a real reading there is no evidence either way, and "nie
        # widzę go" would be a claim about the machine rather than about the
        # measurement that failed.
        return Verdict.unchecked(
            "Nie udało się odczytać listy procesów, więc nie wiem, czy program działa."
        )

    name = str(value.get("process_name") or "")
    if not name:
        return Verdict(ok=False, checked=True,
                       note="Nie wiem, jakiego procesu miałbym szukać.",
                       missing=("process_name",))

    for key in ("running", "already_running", "process_name", "scanned"):
        if outcome.evidence.get(key) != value.get(key):
            return Verdict(ok=False, checked=True,
                           note=f"Dowód nie zgadza się z wynikiem w polu {key}.",
                           missing=(key,))

    if not value.get("running"):
        return Verdict(
            ok=False, checked=True,
            note=f"Uruchomiłem {value.get('target')}, ale procesu {name} nie widzę "
                 f"na liście.",
            missing=(name,),
        )
    if value.get("already_running"):
        return Verdict(ok=True, checked=True,
                       note=f"{value.get('target')} już działał — sprawdziłem na liście "
                            f"procesów.")
    return Verdict(ok=True, checked=True,
                   note=f"Uruchomiłem {value.get('target')} i widzę proces {name}.")


# -------------------------------------------------------------------- recovery


@dataclass(frozen=True, slots=True)
class LaunchReconciler:
    """After a crash mid-launch: read the table, and claim only what it shows.

    The program being up does not prove GARIS started it — a person can click an
    icon while the engine is down — so this settles the *state* and leaves the
    cause `UNKNOWN`. Which is also what stops the effect being repeated: only
    `NOT_STARTED` and `NOT_APPLIED` license running an external effect again, and
    this is neither.
    """

    def inspection_for(self, effect: EffectRecord) -> RecoveryInspection:
        name = str((effect.goal or {}).get("process_name") or "")
        return RecoveryInspection(
            target=CapabilityTarget(PROCESS_LIST.id),
            arguments={"name": name} if name else {},
            purpose="Sprawdzam na liście procesów, czy program działa.",
        )

    def assess(
        self, effect: EffectRecord, observation: Observation
    ) -> RecoveryAssessment:
        name = str((effect.goal or {}).get("process_name") or "")
        value = observation.value if isinstance(observation.value, dict) else {}
        ids = evidence_ids_of(observation.evidence)

        if not observation.ok or not name or "processes" not in value:
            return RecoveryAssessment(
                RecoveryStatus.STILL_UNKNOWN, EffectDisposition.UNKNOWN,
                Verification(goal_met=False, uncertain=True),
                reason="Nie udało mi się odczytać listy procesów.",
                evidence_ids=ids,
            )

        found = running(value.get("processes") or [], name)
        return RecoveryAssessment(
            RecoveryStatus.RESOLVED_GOAL_ONLY, EffectDisposition.UNKNOWN,
            Verification(
                goal_met=bool(found), checked=True, checked_by="rules",
                reason=(f"Proces {name} jest na liście." if found
                        else f"Procesu {name} nie ma na liście."),
                unmet=() if found else (name,),
            ),
            reason="Sprawdziłem listę procesów; nie wiem, czy to moje uruchomienie.",
            evidence_ids=ids,
        )


def _string_list(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(a, str) for a in value)


LAUNCH = REGISTRY.add(
    Capability(
        id=LAUNCH_ID,
        version=1,
        summary="Uruchamia program i sprawdza na liście procesów, czy działa.",
        risk=Risk.REVERSIBLE,
        permission=Permission.PROCESS,
        effects=frozenset({Effect.EXEC}),
        reversible=True,
        executor=_launch,
        verifier=verify_launch,
        inputs=(
            Field("target", "str", "Program, ścieżka albo skrót"),
            Field("arguments", "list", "Argumenty wywołania", required=False,
                  check=_string_list),
            Field("process_name", "str",
                  "Czego szukać na liście procesów, jeśli nazwa różni się od programu",
                  required=False),
        ),
        outputs=(
            Field("target", "str", "Co uruchamiano"),
            Field("process_name", "str", "Czego szukano na liście"),
            Field("running", "bool", "Czy proces jest na liście"),
            Field("already_running", "bool", "Czy działał, zanim cokolwiek zrobiłem"),
            Field("pids", "list", "Znalezione identyfikatory procesów"),
            Field("spawned_pid", "int", "Co zostało wystartowane", required=False),
            Field("waited_ms", "int", "Jak długo czekano na pojawienie się", required=False),
        ),
        evidence=(
            Field("platform", "str", "System, na którym mierzono"),
            Field("process_name", "str", "Czego szukano"),
            Field("running", "bool", "Czy proces widziano",
                  check=lambda v: isinstance(v, bool)),
            # A scan of zero processes is a failed reading, not an empty machine.
            Field("scanned", "int", "Ile procesów odczytano",
                  check=lambda v: isinstance(v, int) and v > 0),
        ),
        # The reading works anywhere this codebase runs, and so does the launch.
        # The `windows.` prefix says where it matters, not where it functions.
        platforms=("win32", "linux", "darwin"),
        exposed=True,
        reconciler=LaunchReconciler(),
        goal_of=lambda args: {
            "process_name": (
                str(args.get("process_name") or "").strip()
                or process_name_for(str(args.get("target", "")))
            ),
            "target": str(args.get("target", "")),
        },
    )
)


__all__ = [
    "APPEAR_TIMEOUT",
    "LAUNCH",
    "LaunchReconciler",
    "process_name_for",
    "running",
    "verify_launch",
]
