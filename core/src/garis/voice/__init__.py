"""Voice: the primary way GARIS is meant to be used.

This package owns the *decisions* — who may talk, when a turn ends, what counts as
speech in this room, which road carries the conversation. The audio hardware and
the speech models sit behind narrow interfaces so those decisions can be tested,
and so replacing an engine never changes how the conversation behaves.

``VoiceService`` is the piece the rest of GARIS talks to: feed it audio frames and
transcripts, and it emits goals for the agent and state for the orb.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import VoiceConfig
from ..events import EventBus, Topic
from ..models import ModelRouter, Privacy
from .audio import (
    Calibration,
    Calibrator,
    Device,
    DeviceKind,
    DeviceRegistry,
    dbfs,
    enumerate_devices,
    normalised_level,
)
from .engines import (
    RealtimeVoice,
    RecordingTextToSpeech,
    Road,
    ScriptedSpeechToText,
    SpeechToText,
    TextToSpeech,
    VoicePlan,
    choose_road,
)
from .session import Trigger, VoiceSession, VoiceState
from .wake import TranscriptDetector, WakeMatch, WakeWordMatcher


@dataclass
class VoiceService:
    """Wires the wake word, the room, the session and the engines together.

    Deliberately not an audio loop: frames arrive from whatever backend is in use.
    That keeps this testable and lets the same logic serve a microphone, a phone
    streaming audio in, or a scripted test.
    """

    config: VoiceConfig
    bus: EventBus | None = None
    router: ModelRouter | None = None
    devices: DeviceRegistry = field(default_factory=DeviceRegistry)
    session: VoiceSession = field(init=False)
    wake: WakeWordMatcher = field(init=False)
    calibration: Calibration = field(init=False)
    plan: VoicePlan = field(default_factory=lambda: VoicePlan(Road.UNAVAILABLE))

    on_turn: Callable[[str], Any] | None = None
    _calibrator: Calibrator | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.session = VoiceSession(bus=self.bus, barge_in=self.config.barge_in)
        self.wake = WakeWordMatcher(phrase=self.config.wake_word)
        self.calibration = Calibration(
            noise_floor_dbfs=self.config.noise_floor_dbfs,
            threshold_dbfs=self.config.noise_floor_dbfs + 12.0,
        )
        self.detector = TranscriptDetector(self.wake)

    # ------------------------------------------------------------------ setup

    def refresh_devices(self) -> DeviceRegistry:
        self.devices = enumerate_devices()
        self.devices.preferred_input = self.config.input_device
        self.devices.preferred_output = self.config.output_device
        return self.devices

    def plan_road(self, *, online: bool = True, local_ready: bool = False) -> VoicePlan:
        """Decide how this conversation will be carried."""
        if self.router is None:
            self.plan = VoicePlan(Road.UNAVAILABLE, reason="brak routera modeli")
            return self.plan
        self.plan = choose_road(
            self.router,
            privacy=Privacy.ANY,
            prefer=self.config.engine,
            online=online,
            local_ready=local_ready,
        )
        return self.plan

    def set_wake_word(self, phrase: str) -> None:
        """Takes effect immediately — a settings change the user cannot verify is a bug."""
        self.config.wake_word = phrase
        self.wake.set_phrase(phrase)

    # ------------------------------------------------------------ calibration

    def start_calibration(self) -> None:
        self._calibrator = Calibrator()

    def calibration_frame(self, samples: list[float]) -> float:
        if self._calibrator is None:
            self.start_calibration()
        assert self._calibrator is not None
        return self._calibrator.add_frame(samples)

    def finish_calibration(self) -> Calibration:
        if self._calibrator is None:
            return self.calibration
        self.calibration = self._calibrator.result()
        self.config.noise_floor_dbfs = self.calibration.noise_floor_dbfs
        self._calibrator = None
        if self.bus is not None:
            self.bus.emit(Topic.VOICE_STATE, state=self.session.state.value,
                          kind="calibrated", **self.calibration.to_dict())
        return self.calibration

    # ----------------------------------------------------------------- audio

    def feed_frame(self, samples: list[float]) -> float:
        """One frame of microphone audio. Returns the 0..1 level for the orb."""
        level_dbfs = dbfs(samples)
        speaking = self.calibration.is_speech(level_dbfs)
        self.session.audio_tick(speaking=speaking)
        return normalised_level(level_dbfs)

    def feed_transcript(self, text: str) -> WakeMatch | None:
        """A transcript arrived.

        Asleep, it is only checked for the wake word — nothing is acted on until
        GARIS has been addressed. Listening, it becomes part of the turn.
        """
        if self.session.state is VoiceState.ASLEEP:
            if not self.config.wake_word_enabled:
                return None
            match = self.detector.feed_text(text)
            if match:
                self.session.wake(Trigger.WAKE_WORD)
                if match.remainder:
                    # "Garis, sprawdź dysk" is one sentence, not a wake word
                    # followed by a pause.
                    self.session.heard(match.remainder)
                return match
            return match

        if self.session.state is VoiceState.LISTENING:
            self.session.heard(text)
        return None

    def push_to_talk(self, *, held: bool) -> None:
        if held:
            self.session.wake(Trigger.PUSH_TO_TALK)
        elif self.session.listening:
            self.finish_turn()

    def finish_turn(self) -> str:
        """Close the turn and hand the text to whoever is listening for goals."""
        event = self.session.end_turn()
        text = str(event.detail.get("text", ""))
        if text and self.on_turn is not None:
            self.on_turn(text)
        return text

    # --------------------------------------------------------------- speaking

    def speak(self, text: str) -> None:
        self.session.start_speaking(text)

    def interrupted(self) -> None:
        self.session.stop_speaking(reason="przerwane przez użytkownika")

    def finished(self, *, keep_floor: bool = True) -> None:
        self.session.finished_speaking(keep_floor=keep_floor)

    # ------------------------------------------------------------ inspection

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "state": self.session.state.value,
            "wake": self.wake.to_dict(),
            "wake_word_enabled": self.config.wake_word_enabled,
            "barge_in": self.config.barge_in,
            "interruptions": self.session.interruptions,
            "calibration": self.calibration.to_dict(),
            "plan": self.plan.to_dict(),
            "devices": self.devices.to_dict(),
        }


__all__ = [
    "Calibration",
    "Calibrator",
    "Device",
    "DeviceKind",
    "DeviceRegistry",
    "RealtimeVoice",
    "RecordingTextToSpeech",
    "Road",
    "ScriptedSpeechToText",
    "SpeechToText",
    "TextToSpeech",
    "TranscriptDetector",
    "Trigger",
    "VoicePlan",
    "VoiceService",
    "VoiceSession",
    "VoiceState",
    "WakeMatch",
    "WakeWordMatcher",
    "choose_road",
    "dbfs",
    "enumerate_devices",
    "normalised_level",
]
