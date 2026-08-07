"""What the speakers are actually set to — measured, never assumed.

The first capability that reads real Windows state, and the one the rest of the
audio work leans on: `audio.set` will be judged by reading back through this,
and recovery after a crash will inspect with it. So it has to be boring and it
has to be true.

Two rules that look like details and are not:

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
from .base import Capability, Field, Invocation, Outcome, Permission, Risk, Verdict
from .registry import REGISTRY


def percent_of(scalar: float) -> int:
    """The one conversion. Used by the capability and by its verifier.

    Two implementations of this would eventually disagree on a boundary, and the
    disagreement would surface as a capability that cannot verify its own
    output.
    """
    return round(scalar * 100)


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

        endpoint = device.EndpointVolume
        scalar = float(endpoint.GetMasterVolumeLevelScalar())
        muted = bool(endpoint.GetMute())
        name = device.FriendlyName or ""

        return AudioReading(
            endpoint_id=str(device.id or ""),
            endpoint_name=str(name),
            volume_scalar=scalar,
            muted=muted,
        )
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


# ----------------------------------------------------------------- verification


async def verify_master_reading(invocation: Invocation, outcome: Outcome) -> Verdict:
    """Arithmetic on what came back. No model, no benefit of the doubt.

    Deliberately not `evidence_verifier()`: the checks here are about *relations*
    between fields — the percentage following from the scalar, the evidence
    agreeing with the value — and a per-field schema cannot express those.
    """
    if not outcome.ok:
        return Verdict(ok=False, checked=True,
                       note=outcome.error or "Nie udało się odczytać głośności.")

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

    return Verdict(ok=True, checked=True, note="Odczytałem i sprawdziłem głośność.")


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


MASTER_GET = REGISTRY.add(
    Capability(
        id="windows.audio.master.get",
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
        outputs=(
            Field("endpoint_id", "str", "Identyfikator urządzenia odtwarzania"),
            Field("endpoint_name", "str", "Nazwa urządzenia", required=False),
            Field("volume_scalar", "float", "Poziom od 0.0 do 1.0"),
            Field("volume_percent", "int", "Poziom w procentach"),
            Field("muted", "bool", "Czy dźwięk jest wyciszony"),
        ),
        evidence=(
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
        ),
        platforms=("win32",),
        exposed=True,
    )
)


__all__ = [
    "MASTER_GET",
    "AudioReading",
    "describe",
    "percent_of",
    "verify_master_reading",
]
