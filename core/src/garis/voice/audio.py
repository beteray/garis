"""Devices, levels and noise calibration.

The maths lives here rather than in the audio backend so it can be tested without
a sound card, and so swapping the backend (sounddevice today, WASAPI later) does
not change how loud "loud enough" is.

Calibration exists because a fixed threshold is wrong in every room. A quiet
office and a desk next to a fan differ by 20 dB; the same number cannot serve
both, and getting it wrong means either GARIS never hears the user or it wakes up
to the fan.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SILENCE_DBFS = -90.0
# How far above the measured room noise a sound must be to count as speech.
SPEECH_MARGIN_DB = 12.0
# Rooms are never as quiet as the quietest single frame; use a low percentile.
NOISE_PERCENTILE = 0.25
MIN_CALIBRATION_FRAMES = 8


class DeviceKind(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    kind: DeviceKind
    channels: int = 1
    sample_rate: int = 16_000
    default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "default": self.default,
        }


def rms(samples: list[float]) -> float:
    """Root mean square of -1..1 samples."""
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def dbfs(samples: list[float]) -> float:
    """Frame loudness in dBFS. Digital silence is a floor, not minus infinity."""
    level = rms(samples)
    if level <= 1e-9:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20 * math.log10(level))


def normalised_level(value_dbfs: float, floor: float = -60.0) -> float:
    """Map dBFS onto 0..1 for the orb's listening rings."""
    if value_dbfs <= floor:
        return 0.0
    return min(1.0, (value_dbfs - floor) / -floor)


@dataclass(slots=True)
class Calibration:
    """The result of listening to a quiet room for a moment."""

    noise_floor_dbfs: float = -45.0
    threshold_dbfs: float = -33.0
    frames: int = 0
    peak_dbfs: float = SILENCE_DBFS

    @property
    def usable(self) -> bool:
        return self.frames >= MIN_CALIBRATION_FRAMES

    @property
    def noisy_room(self) -> bool:
        """Loud enough that speech detection will struggle and the user should know."""
        return self.noise_floor_dbfs > -30.0

    def is_speech(self, frame_dbfs: float) -> bool:
        return frame_dbfs >= self.threshold_dbfs

    def to_dict(self) -> dict[str, Any]:
        return {
            "noise_floor_dbfs": round(self.noise_floor_dbfs, 1),
            "threshold_dbfs": round(self.threshold_dbfs, 1),
            "frames": self.frames,
            "peak_dbfs": round(self.peak_dbfs, 1),
            "noisy_room": self.noisy_room,
        }


class Calibrator:
    """Collects frames of room noise and derives a speech threshold."""

    def __init__(self, *, margin_db: float = SPEECH_MARGIN_DB) -> None:
        self.margin_db = margin_db
        self._frames: list[float] = []

    def add_frame(self, samples: list[float]) -> float:
        value = dbfs(samples)
        self._frames.append(value)
        return value

    def add_dbfs(self, value: float) -> None:
        self._frames.append(value)

    def result(self) -> Calibration:
        if not self._frames:
            return Calibration(frames=0)

        ordered = sorted(self._frames)
        # A percentile rather than the minimum: one freakishly quiet frame would
        # otherwise set the floor below the actual room and make everything
        # register as speech.
        index = min(len(ordered) - 1, int(len(ordered) * NOISE_PERCENTILE))
        floor = ordered[index]
        return Calibration(
            noise_floor_dbfs=floor,
            threshold_dbfs=floor + self.margin_db,
            frames=len(self._frames),
            peak_dbfs=ordered[-1],
        )

    def reset(self) -> None:
        self._frames.clear()


@dataclass
class DeviceRegistry:
    """What the machine has, and what the user picked.

    Backed by a real enumeration on Windows; a plain list here so device
    selection, fallback and "the mic was unplugged" are testable.
    """

    devices: list[Device] = field(default_factory=list)
    preferred_input: str = ""
    preferred_output: str = ""

    def inputs(self) -> list[Device]:
        return [d for d in self.devices if d.kind is DeviceKind.INPUT]

    def outputs(self) -> list[Device]:
        return [d for d in self.devices if d.kind is DeviceKind.OUTPUT]

    def resolve(self, kind: DeviceKind) -> Device | None:
        """The device to actually use.

        Falls back to the system default when the chosen one is gone — an unplugged
        headset must not leave GARIS deaf until someone opens settings.
        """
        candidates = self.inputs() if kind is DeviceKind.INPUT else self.outputs()
        if not candidates:
            return None
        wanted = self.preferred_input if kind is DeviceKind.INPUT else self.preferred_output
        if wanted:
            for device in candidates:
                if device.id == wanted or device.name == wanted:
                    return device
        for device in candidates:
            if device.default:
                return device
        return candidates[0]

    def missing_preferred(self, kind: DeviceKind) -> bool:
        """True when the user's choice is not currently present."""
        wanted = self.preferred_input if kind is DeviceKind.INPUT else self.preferred_output
        if not wanted:
            return False
        pool = self.inputs() if kind is DeviceKind.INPUT else self.outputs()
        return not any(d.id == wanted or d.name == wanted for d in pool)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": [d.to_dict() for d in self.inputs()],
            "outputs": [d.to_dict() for d in self.outputs()],
            "using_input": (used := self.resolve(DeviceKind.INPUT)) and used.to_dict(),
            "using_output": (out := self.resolve(DeviceKind.OUTPUT)) and out.to_dict(),
            "input_missing": self.missing_preferred(DeviceKind.INPUT),
            "output_missing": self.missing_preferred(DeviceKind.OUTPUT),
        }


def enumerate_devices() -> DeviceRegistry:
    """Ask the OS what it has. Empty registry when no audio stack is present."""
    try:
        import sounddevice  # type: ignore[import-not-found]
    except Exception:
        return DeviceRegistry()

    registry = DeviceRegistry()
    try:
        default_in, default_out = sounddevice.default.device
    except Exception:
        default_in = default_out = -1

    for index, raw in enumerate(sounddevice.query_devices()):
        if raw.get("max_input_channels", 0) > 0:
            registry.devices.append(
                Device(
                    id=str(index),
                    name=str(raw.get("name", f"wejście {index}")),
                    kind=DeviceKind.INPUT,
                    channels=int(raw.get("max_input_channels", 1)),
                    sample_rate=int(raw.get("default_samplerate", 16_000)),
                    default=index == default_in,
                )
            )
        if raw.get("max_output_channels", 0) > 0:
            registry.devices.append(
                Device(
                    id=str(index),
                    name=str(raw.get("name", f"wyjście {index}")),
                    kind=DeviceKind.OUTPUT,
                    channels=int(raw.get("max_output_channels", 2)),
                    sample_rate=int(raw.get("default_samplerate", 48_000)),
                    default=index == default_out,
                )
            )
    return registry


__all__ = [
    "MIN_CALIBRATION_FRAMES",
    "SPEECH_MARGIN_DB",
    "Calibration",
    "Calibrator",
    "Device",
    "DeviceKind",
    "DeviceRegistry",
    "dbfs",
    "enumerate_devices",
    "normalised_level",
    "rms",
]
