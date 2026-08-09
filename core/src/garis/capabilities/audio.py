"""What the speakers are actually set to — measured, never assumed — and moved.

The reading came first and everything else leans on it: the two writers below are
judged by reading back through it, and recovery after a crash inspects with it.
So it has to be boring and it has to be true.

**A write is not finished when the API returns.** `SetMasterVolumeLevelScalar`
returning `S_OK` says a call succeeded, not that the machine is at 30%. So both
writers read the endpoint back through the same measurement the reader uses, and
the verifier compares that reading against what was asked for. A write whose
readback cannot be taken is `uncertain`, never successful.

**A failed write is not a write that did not happen.** Failing before the
endpoint is touched and failing after it are different facts about the world, and
this module says which one it met — `NOT_APPLIED` before the call, `APPLIED` when
the call returned and only the readback broke, `UNKNOWN` when the call itself
raised. Rounding all three to "it failed" is how one changed machine gets changed
again.

Two more rules that look like details and are not:

**One measurement, one conversion.** Windows is asked for the scalar and only
the scalar; the percentage is derived from it by `percent_of`, which the
verifier also uses. Reading a percentage separately would produce two numbers
that can disagree, and then the check would be "do my two readings match"
instead of "is my reading true".

**No default device is not silence.** A dead audio service, a missing endpoint,
a COM apartment that would not start — every one of those is a checked failure
that says what happened. Rounding any of them to 0% would be GARIS reporting a
quiet computer when what it means is that it could not look.
"""

from __future__ import annotations

import asyncio
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..errors import ExecutionError, Unsupported
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
from .base import Capability, Field, Invocation, Outcome, Permission, Risk, Verdict
from .registry import REGISTRY

MASTER_GET_ID = "windows.audio.master.get"
MASTER_SET_ID = "windows.audio.master.set"
MASTER_MUTE_ID = "windows.audio.master.mute"

#: How far the readback may sit from the request and still count as done.
#:
#: Not slack for its own sake. A Windows endpoint quantises the master level to
#: the steps its driver exposes, so asking for 30% and measuring 29% or 31% is
#: the hardware answering honestly. Anything wider would start hiding a write
#: that landed somewhere else entirely, which is the failure this tolerance
#: exists alongside — not the one it is allowed to cause.
SET_TOLERANCE_POINTS = 1


def percent_of(scalar: float) -> int:
    """The one conversion. Used by the capability and by its verifier.

    Two implementations of this would eventually disagree on a boundary, and the
    disagreement would surface as a capability that cannot verify its own
    output.

    Half-up rather than `round()`, which is banker's: it would turn 42.5 into 42
    and 43.5 into 44. Both are defensible for statistics and neither is
    explainable to someone looking at a volume slider.
    """
    return math.floor(scalar * 100 + 0.5)


@dataclass(frozen=True, slots=True)
class AudioReading:
    """One observation of the default render endpoint.

    `endpoint_id` is the part that matters later: `audio.set` and recovery both
    have to be able to say "the thing I measured is the thing I changed", and a
    friendly name is not an identity — two headsets can share one.
    """

    endpoint_id: str
    endpoint_name: str
    volume_scalar: float
    muted: bool

    @property
    def volume_percent(self) -> int:
        return percent_of(self.volume_scalar)

    def to_value(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "endpoint_name": self.endpoint_name,
            "volume_scalar": self.volume_scalar,
            "volume_percent": self.volume_percent,
            "muted": self.muted,
        }

    def to_evidence(self) -> dict[str, Any]:
        """What was measured, and nothing about the machine that was not asked
        for. The endpoint id is an opaque Windows string, not a serial number."""
        return {
            "endpoint_id": self.endpoint_id,
            "volume_scalar": self.volume_scalar,
            "volume_percent": self.volume_percent,
            "muted": self.muted,
            "platform": sys.platform,
        }


# --------------------------------------------------------------------- reading


def _observe(device: Any, endpoint: Any) -> AudioReading:
    """One measurement of an endpoint, used by the reader and by both writers.

    Shared so that "what GARIS reports" and "what GARIS checks its own write
    against" cannot drift into two slightly different readings of the same
    device.
    """
    return AudioReading(
        endpoint_id=str(device.id or ""),
        endpoint_name=str(device.FriendlyName or ""),
        volume_scalar=float(endpoint.GetMasterVolumeLevelScalar()),
        muted=bool(endpoint.GetMute()),
    )


def _read_windows() -> AudioReading:
    """Windows Core Audio, through the current pycaw surface.

    `AudioDevice.EndpointVolume` performs the `Activate` and `QueryInterface`
    itself, so the old `cast(interface, POINTER(IAudioEndpointVolume))` dance
    from the examples is not needed and is not done here.

    COM is initialised on *this* thread because the read runs in a worker: an
    apartment belongs to a thread, and comtypes only initialises the one that
    imported it.
    """
    import comtypes
    from pycaw.utils import AudioUtilities

    try:
        comtypes.CoInitialize()
    except OSError as exc:  # pragma: no cover - needs Windows
        raise ExecutionError(
            "Nie udało się uruchomić warstwy COM systemu Windows."
        ) from exc

    try:
        device = AudioUtilities.GetSpeakers()
        if device is None:
            # Not silence. Windows has no default playback device at all —
            # every speaker unplugged, or the audio service is down.
            raise ExecutionError(
                "Windows nie wskazuje żadnego domyślnego urządzenia odtwarzania."
            )

        return _observe(device, device.EndpointVolume)
    except ExecutionError:
        raise
    except Exception as exc:  # pragma: no cover - needs Windows
        # COMError, a vanished endpoint, a malformed result. All of them mean
        # "I could not read it", and none of them means "the volume is zero".
        raise ExecutionError(
            f"Nie udało się odczytać głośności z Windows: {exc}"
        ) from exc
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:  # pragma: no cover - best effort
            pass


def _unsupported() -> AudioReading:
    raise Unsupported(
        "Odczyt głośności działa tylko na Windows.", tool="windows.audio.master.get"
    )


#: The seam. Swapped by unit tests; never by production code, which selects the
#: backend once, here, from the platform.
_BACKEND: Callable[[], AudioReading] = (
    _read_windows if sys.platform == "win32" else _unsupported
)


async def _get_master(invocation: Invocation) -> Outcome:
    try:
        reading = await asyncio.to_thread(_BACKEND)
    except Unsupported as exc:
        return Outcome(ok=False, error=str(exc), evidence={"platform": sys.platform})
    except ExecutionError as exc:
        # A checked failure: something is wrong and GARIS can say what.
        return Outcome(ok=False, error=str(exc), evidence={"platform": sys.platform})

    return Outcome(ok=True, value=reading.to_value(), evidence=reading.to_evidence())


# --------------------------------------------------------------------- writing


@dataclass(frozen=True, slots=True)
class AudioChange:
    """A write, with the endpoint measured on both sides of it.

    `before` is not decoration: it is what lets GARIS say "było 20%, jest 30%"
    from measurement rather than from the request, and what a person needs in
    order to put the change back.
    """

    before: AudioReading
    after: AudioReading


class WriteRefused(ExecutionError):
    """A write that did not finish, carrying what it knows about the world.

    The disposition is the whole point. An exception on its own says the code
    failed and says nothing about the machine, and "it raised, so nothing
    happened" is the assumption that turns one changed endpoint into two changes.
    """

    def __init__(
        self,
        message: str,
        *,
        disposition: EffectDisposition,
        uncertain: bool = False,
    ) -> None:
        super().__init__(message, retryable=disposition.permits_retry)
        self.disposition = disposition
        self.uncertain = uncertain


def _write_windows(scalar: float | None, muted: bool | None) -> AudioChange:
    """Set the level and/or the mute flag, then measure what the endpoint says.

    Structured around one question asked three times: *has the endpoint been
    touched yet?* Everything before the first setter is `NOT_APPLIED` — nothing
    outside GARIS can have moved. The setters themselves are `UNKNOWN`, because a
    COM call that raises may still have been delivered. Afterwards it is
    `APPLIED`, whatever the readback does.
    """
    import comtypes
    from pycaw.utils import AudioUtilities

    try:
        comtypes.CoInitialize()
    except OSError as exc:  # pragma: no cover - needs Windows
        raise WriteRefused(
            "Nie udało się uruchomić warstwy COM systemu Windows.",
            disposition=EffectDisposition.NOT_APPLIED,
        ) from exc

    try:
        try:
            device = AudioUtilities.GetSpeakers()
            if device is None:
                raise WriteRefused(
                    "Windows nie wskazuje żadnego domyślnego urządzenia odtwarzania.",
                    disposition=EffectDisposition.NOT_APPLIED,
                )
            endpoint = device.EndpointVolume
            before = _observe(device, endpoint)
        except WriteRefused:
            raise
        except Exception as exc:  # pragma: no cover - needs Windows
            raise WriteRefused(
                f"Nie udało się otworzyć urządzenia odtwarzania: {exc}",
                disposition=EffectDisposition.NOT_APPLIED,
            ) from exc

        try:
            if scalar is not None:
                endpoint.SetMasterVolumeLevelScalar(scalar, None)
            if muted is not None:
                endpoint.SetMute(bool(muted), None)
        except Exception as exc:  # pragma: no cover - needs Windows
            raise WriteRefused(
                f"Nie udało się zmienić ustawień dźwięku: {exc}",
                disposition=EffectDisposition.UNKNOWN,
                uncertain=True,
            ) from exc

        try:
            after = _observe(device, endpoint)
        except Exception as exc:  # pragma: no cover - needs Windows
            raise WriteRefused(
                f"Zmiana poszła do Windows, ale nie udało się sprawdzić wyniku: {exc}",
                disposition=EffectDisposition.APPLIED,
                uncertain=True,
            ) from exc

        return AudioChange(before=before, after=after)
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:  # pragma: no cover - best effort
            pass


def _unsupported_write(scalar: float | None, muted: bool | None) -> AudioChange:
    raise Unsupported(
        "Zmiana ustawień dźwięku działa tylko na Windows.", tool=MASTER_SET_ID
    )


#: The writing seam, chosen once from the platform. Same rule as `_BACKEND`.
_WRITE_BACKEND: Callable[[float | None, bool | None], AudioChange] = (
    _write_windows if sys.platform == "win32" else _unsupported_write
)


async def _apply(
    *,
    scalar: float | None,
    muted: bool | None,
    requested: dict[str, Any],
) -> Outcome:
    """One shape for both writers: attempt, measure, report the facts."""
    try:
        change = await asyncio.to_thread(_WRITE_BACKEND, scalar, muted)
    except Unsupported as exc:
        # This host has no Core Audio to touch, so nothing outside GARIS moved.
        return Outcome(
            ok=False, error=str(exc), evidence={"platform": sys.platform},
            disposition=EffectDisposition.NOT_APPLIED,
        )
    except WriteRefused as exc:
        return Outcome(
            ok=False, error=str(exc), evidence={"platform": sys.platform},
            uncertain=exc.uncertain, disposition=exc.disposition,
        )
    except ExecutionError as exc:
        # Something the backend did not classify. It got past the call boundary,
        # so the only honest answer about the endpoint is that nobody knows.
        return Outcome(
            ok=False, error=str(exc), evidence={"platform": sys.platform},
            uncertain=True, disposition=EffectDisposition.UNKNOWN,
        )

    return Outcome(
        ok=True,
        value={
            **change.after.to_value(),
            **requested,
            "previous_percent": change.before.volume_percent,
            "previous_muted": change.before.muted,
        },
        evidence={**change.after.to_evidence(), **requested},
        # The setter returned and the endpoint was measured afterwards. Whether
        # the measurement is what was asked for is the verifier's question, not
        # this one's.
        disposition=EffectDisposition.APPLIED,
    )


async def _set_master(invocation: Invocation) -> Outcome:
    percent = int(invocation.get("percent"))
    return await _apply(
        scalar=percent / 100,
        muted=None,
        requested={"requested_percent": percent},
    )


async def _mute_master(invocation: Invocation) -> Outcome:
    muted = bool(invocation.get("muted"))
    return await _apply(
        scalar=None,
        muted=muted,
        requested={"requested_muted": muted},
    )


# ----------------------------------------------------------------- verification


def _reading_fault(outcome: Outcome) -> Verdict | None:
    """Everything wrong a measurement can be, or `None` when it is sound.

    Shared by the reader and by both writers, because a write is judged on a
    reading and a reading GARIS would refuse to report is not one it may accept
    as proof of its own work.

    Deliberately not `evidence_verifier()`: the checks are about *relations*
    between fields — the percentage following from the scalar, the evidence
    agreeing with the value — and a per-field schema cannot express those.
    """
    value = outcome.value
    if not isinstance(value, dict):
        return Verdict(ok=False, checked=True, note="Odczyt nie zwrócił danych.")

    missing = [k for k in ("endpoint_id", "volume_scalar", "volume_percent", "muted")
               if k not in value]
    if missing:
        return Verdict(ok=False, checked=True,
                       note="Odczyt jest niekompletny: " + ", ".join(missing),
                       missing=tuple(missing))

    if not str(value["endpoint_id"]).strip():
        # Without identity, a later `audio.set` cannot prove it changed the same
        # endpoint this reading measured.
        return Verdict(ok=False, checked=True,
                       note="Odczyt nie mówi, którego urządzenia dotyczy.",
                       missing=("endpoint_id",))

    scalar = value["volume_scalar"]
    if isinstance(scalar, bool) or not isinstance(scalar, (int, float)):
        return Verdict(ok=False, checked=True, note="Poziom głośności nie jest liczbą.")
    if not math.isfinite(float(scalar)):
        # NaN fails every comparison silently, including the range check below,
        # so it is refused on being non-finite rather than on being out of range.
        return Verdict(ok=False, checked=True,
                       note="Poziom głośności nie jest skończoną liczbą.")
    if not 0.0 <= float(scalar) <= 1.0:
        return Verdict(ok=False, checked=True,
                       note=f"Poziom głośności poza zakresem: {scalar}.")

    percent = value["volume_percent"]
    if isinstance(percent, bool) or not isinstance(percent, int):
        return Verdict(ok=False, checked=True, note="Procent głośności nie jest liczbą całkowitą.")
    if not 0 <= percent <= 100:
        return Verdict(ok=False, checked=True, note=f"Procent głośności poza zakresem: {percent}.")
    if percent != percent_of(float(scalar)):
        return Verdict(
            ok=False, checked=True,
            note=f"Procent {percent} nie wynika ze zmierzonego poziomu {scalar}.",
        )

    if not isinstance(value["muted"], bool):
        return Verdict(ok=False, checked=True, note="Wyciszenie nie jest wartością logiczną.")

    for key in ("endpoint_id", "volume_scalar", "volume_percent", "muted"):
        if outcome.evidence.get(key) != value[key]:
            return Verdict(
                ok=False, checked=True,
                note=f"Dowód nie zgadza się z wynikiem w polu {key}.",
                missing=(key,),
            )

    return None


async def verify_master_reading(invocation: Invocation, outcome: Outcome) -> Verdict:
    """The reading is sound. No model, no benefit of the doubt."""
    if not outcome.ok:
        return Verdict(ok=False, checked=True,
                       note=outcome.error or "Nie udało się odczytać głośności.")
    fault = _reading_fault(outcome)
    if fault is not None:
        return fault
    return Verdict(ok=True, checked=True, note="Odczytałem i sprawdziłem głośność.")


async def verify_master_set(invocation: Invocation, outcome: Outcome) -> Verdict:
    """Did the endpoint end up where it was asked to?

    Judged on the readback, never on the setter having returned. `S_OK` means a
    call was accepted; this is the only thing in the pipeline that looks at what
    the machine actually says afterwards.
    """
    if not outcome.ok:
        return Verdict(ok=False, checked=True,
                       note=outcome.error or "Nie udało się ustawić głośności.")
    fault = _reading_fault(outcome)
    if fault is not None:
        return fault

    value = outcome.value
    wanted = value.get("requested_percent")
    if isinstance(wanted, bool) or not isinstance(wanted, int):
        # Without the request there is nothing to compare against, and a check
        # with nothing to compare against is not a check.
        return Verdict(ok=False, checked=True,
                       note="Nie wiem, o jaką głośność prosiłeś.",
                       missing=("requested_percent",))
    if outcome.evidence.get("requested_percent") != wanted:
        return Verdict(ok=False, checked=True,
                       note="Dowód nie zgadza się z tym, o co prosiłeś.",
                       missing=("requested_percent",))

    measured = value["volume_percent"]
    drift = abs(measured - wanted)
    if drift > SET_TOLERANCE_POINTS:
        return Verdict(
            ok=False, checked=True,
            note=f"Ustawiłem głośność, ale urządzenie pokazuje {measured}% "
                 f"zamiast {wanted}%.",
            missing=(f"głośność {wanted}%",),
        )
    return Verdict(ok=True, checked=True,
                   note=f"Ustawiłem głośność i sprawdziłem: {measured}%.")


async def verify_master_mute(invocation: Invocation, outcome: Outcome) -> Verdict:
    """Same rule, one boolean: the endpoint reads back as it was asked to."""
    if not outcome.ok:
        return Verdict(ok=False, checked=True,
                       note=outcome.error or "Nie udało się zmienić wyciszenia.")
    fault = _reading_fault(outcome)
    if fault is not None:
        return fault

    value = outcome.value
    wanted = value.get("requested_muted")
    if not isinstance(wanted, bool):
        return Verdict(ok=False, checked=True,
                       note="Nie wiem, o co prosiłeś: wyciszyć czy odciszyć.",
                       missing=("requested_muted",))
    if outcome.evidence.get("requested_muted") != wanted:
        return Verdict(ok=False, checked=True,
                       note="Dowód nie zgadza się z tym, o co prosiłeś.",
                       missing=("requested_muted",))

    if value["muted"] is not wanted:
        return Verdict(
            ok=False, checked=True,
            note="Wyciszenie nie zmieniło się na to, o co prosiłeś.",
            missing=("wyciszenie" if wanted else "brak wyciszenia",),
        )
    return Verdict(
        ok=True, checked=True,
        note="Wyciszyłem i sprawdziłem." if wanted
        else "Wyłączyłem wyciszenie i sprawdziłem.",
    )


# ------------------------------------------------------------------- recovery


@dataclass(frozen=True, slots=True)
class VolumeReconciler:
    """After a crash: look at the endpoint, and be careful what that proves.

    The reading settles the *state* and never the *cause*. Someone can reach for
    the volume key while the engine is down, so measuring 30% after asking for
    30% is a fact about the machine and not evidence that GARIS moved it. Hence
    `RESOLVED_GOAL_ONLY` with the disposition left `UNKNOWN` — which also means
    the effect is not silently re-run, because only `NOT_STARTED` and
    `NOT_APPLIED` license that.
    """

    field: str = "volume_percent"
    tolerance: int = SET_TOLERANCE_POINTS

    def inspection_for(self, effect: EffectRecord) -> RecoveryInspection:
        return RecoveryInspection(
            target=CapabilityTarget(MASTER_GET_ID),
            arguments={},
            purpose="Sprawdzam, jak są teraz ustawione głośniki.",
        )

    def assess(
        self, effect: EffectRecord, observation: Observation
    ) -> RecoveryAssessment:
        wanted = (effect.goal or {}).get(self.field)
        seen = (
            observation.value.get(self.field)
            if isinstance(observation.value, dict) else None
        )
        ids = evidence_ids_of(observation.evidence)

        if not observation.ok or seen is None or wanted is None:
            return RecoveryAssessment(
                RecoveryStatus.STILL_UNKNOWN, EffectDisposition.UNKNOWN,
                Verification(goal_met=False, uncertain=True),
                reason="Odczyt nie powiedział mi, jak jest teraz ustawiony dźwięk.",
                evidence_ids=ids,
            )

        met, sentence = self._compare(wanted, seen)
        return RecoveryAssessment(
            RecoveryStatus.RESOLVED_GOAL_ONLY, EffectDisposition.UNKNOWN,
            Verification(
                goal_met=met, checked=True, checked_by="rules", reason=sentence,
                unmet=() if met else (str(wanted),),
            ),
            reason="Zmierzyłem stan dźwięku; nie wiem, czy to moja operacja go ustawiła.",
            evidence_ids=ids,
        )

    def _compare(self, wanted: Any, seen: Any) -> tuple[bool, str]:
        if isinstance(wanted, bool) or isinstance(seen, bool):
            met = bool(seen) is bool(wanted)
            state = "włączone" if seen else "wyłączone"
            return met, (
                f"Wyciszenie jest {state}, czyli tak, jak miało być." if met
                else f"Wyciszenie jest {state}, a miało być odwrotnie."
            )
        met = abs(int(seen) - int(wanted)) <= self.tolerance
        return met, (
            f"Głośność wynosi {seen}%, czyli tyle, ile miała." if met
            else f"Głośność wynosi {seen}%, a miała wynosić {wanted}%."
        )


MUTE_RECONCILER = VolumeReconciler(field="muted")


# ------------------------------------------------------------------ presenting


def describe(value: Any) -> str:
    """One sentence, built from the verified structure. Never from prose."""
    if not isinstance(value, dict):
        return ""
    percent = value.get("volume_percent")
    muted = value.get("muted")
    if not isinstance(percent, int) or not isinstance(muted, bool):
        return ""
    if muted:
        return f"Głośność jest ustawiona na {percent}%, ale dźwięk jest wyciszony."
    return f"Głośność: {percent}%."


#: The measurement every audio capability returns, declared once. Each of them
#: reports the same endpoint through the same reading, so describing it three
#: times would be three chances to describe it differently.
_READING_OUTPUTS: tuple[Field, ...] = (
    Field("endpoint_id", "str", "Identyfikator urządzenia odtwarzania"),
    Field("endpoint_name", "str", "Nazwa urządzenia", required=False),
    Field("volume_scalar", "float", "Poziom od 0.0 do 1.0"),
    Field("volume_percent", "int", "Poziom w procentach"),
    Field("muted", "bool", "Czy dźwięk jest wyciszony"),
)

_READING_EVIDENCE: tuple[Field, ...] = (
    Field("endpoint_id", "str", "Zmierzone urządzenie"),
    Field("volume_scalar", "float", "Zmierzony poziom",
          check=lambda v: isinstance(v, (int, float))
          and not isinstance(v, bool)
          and math.isfinite(float(v))
          and 0.0 <= float(v) <= 1.0),
    Field("volume_percent", "int", "Zmierzony poziom w procentach",
          check=lambda v: isinstance(v, int) and not isinstance(v, bool)
          and 0 <= v <= 100),
    Field("muted", "bool", "Zmierzone wyciszenie",
          check=lambda v: isinstance(v, bool)),
)


def _in_range(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


MASTER_GET = REGISTRY.add(
    Capability(
        id=MASTER_GET_ID,
        version=1,
        summary="Odczytuje główną głośność i stan wyciszenia.",
        risk=Risk.READ,
        permission=Permission.SYSTEM_READ,
        # Stated, not implied. Reading the volume changes nothing, and
        # `SYSTEM_READ` would equally cover a capability that changes it.
        effects=frozenset(),
        reversible=True,
        executor=_get_master,
        verifier=verify_master_reading,
        outputs=_READING_OUTPUTS,
        evidence=_READING_EVIDENCE,
        platforms=("win32",),
        exposed=True,
    )
)


MASTER_SET = REGISTRY.add(
    Capability(
        id=MASTER_SET_ID,
        version=1,
        summary="Ustawia główną głośność na zadany procent.",
        # Changes the machine and can be put straight back — which is what
        # `REVERSIBLE` means here, and why this does not need a person's yes.
        risk=Risk.REVERSIBLE,
        permission=Permission.AUDIO,
        effects=frozenset({Effect.SYSTEM_CONFIG}),
        reversible=True,
        executor=_set_master,
        verifier=verify_master_set,
        inputs=(
            # Checked during validation, so 250% is refused before an effect is
            # reserved and long before the endpoint is touched.
            Field("percent", "int", "Docelowy poziom w procentach (0-100)",
                  check=_in_range),
        ),
        outputs=(
            *_READING_OUTPUTS,
            Field("requested_percent", "int", "O jaki poziom prosiłeś"),
            Field("previous_percent", "int", "Poziom zmierzony przed zmianą"),
            Field("previous_muted", "bool", "Wyciszenie zmierzone przed zmianą"),
        ),
        evidence=(
            *_READING_EVIDENCE,
            Field("requested_percent", "int", "O jaki poziom prosiłeś",
                  check=_in_range),
        ),
        platforms=("win32",),
        exposed=True,
        reconciler=VolumeReconciler(),
        goal_of=lambda args: {"volume_percent": int(args["percent"])},
    )
)


MASTER_MUTE = REGISTRY.add(
    Capability(
        id=MASTER_MUTE_ID,
        version=1,
        summary="Wycisza dźwięk albo zdejmuje wyciszenie.",
        risk=Risk.REVERSIBLE,
        permission=Permission.AUDIO,
        effects=frozenset({Effect.SYSTEM_CONFIG}),
        reversible=True,
        executor=_mute_master,
        verifier=verify_master_mute,
        inputs=(Field("muted", "bool", "Czy dźwięk ma być wyciszony"),),
        outputs=(
            *_READING_OUTPUTS,
            Field("requested_muted", "bool", "O co prosiłeś"),
            Field("previous_percent", "int", "Poziom zmierzony przed zmianą"),
            Field("previous_muted", "bool", "Wyciszenie zmierzone przed zmianą"),
        ),
        evidence=(
            *_READING_EVIDENCE,
            Field("requested_muted", "bool", "O co prosiłeś",
                  check=lambda v: isinstance(v, bool)),
        ),
        platforms=("win32",),
        exposed=True,
        reconciler=MUTE_RECONCILER,
        goal_of=lambda args: {"muted": bool(args["muted"])},
    )
)


__all__ = [
    "MASTER_GET",
    "MASTER_MUTE",
    "MASTER_SET",
    "SET_TOLERANCE_POINTS",
    "AudioChange",
    "AudioReading",
    "VolumeReconciler",
    "WriteRefused",
    "describe",
    "percent_of",
    "verify_master_mute",
    "verify_master_reading",
    "verify_master_set",
]
