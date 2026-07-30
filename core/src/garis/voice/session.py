"""The conversation state machine.

Real-time voice is not "record, transcribe, reply". It is a set of rules about who
is allowed to talk right now, and the one that matters most is **barge-in**: when
the user starts speaking, GARIS stops. Mid-word. An assistant that finishes its
sentence first is an assistant people stop talking to.

The machine here is deliberately engine-independent: it makes the same decisions
whether speech is being handled by a provider's realtime API or by local
whisper + Piper. Engines report events; this decides what happens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..events import EventBus, Topic

# How much speech we need before believing the user really is talking. Below this
# a cough or a door closing would cut GARIS off mid-sentence.
BARGE_IN_MS = 220.0

# Silence that ends a turn. Long enough to think mid-sentence, short enough that
# the conversation does not feel laggy.
END_OF_TURN_MS = 700.0

# A wake word opens a window; if nothing follows, GARIS goes back to sleep rather
# than listening to the room indefinitely.
WAKE_WINDOW_MS = 6000.0


class VoiceState(StrEnum):
    ASLEEP = "asleep"          # wake word armed, not listening to content
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    PAUSED = "paused"          # microphone off, by the user's choice


class Trigger(StrEnum):
    WAKE_WORD = "wake_word"
    PUSH_TO_TALK = "push_to_talk"
    HOTKEY = "hotkey"
    TEXT = "text"
    FOLLOW_UP = "follow_up"    # GARIS kept the floor open after answering


@dataclass(slots=True)
class Turn:
    """One thing the user said."""

    text: str
    started_at: float
    ended_at: float
    trigger: Trigger

    @property
    def duration(self) -> float:
        return self.ended_at - self.started_at


@dataclass(slots=True)
class SessionEvent:
    kind: str
    state: VoiceState
    detail: dict[str, Any] = field(default_factory=dict)


class VoiceSession:
    """Owns who is talking. Engines call in; this decides.

    Time is injected so the whole machine — including the barge-in threshold and
    the wake window — is testable without sleeping.
    """

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        barge_in: bool = True,
        barge_in_ms: float = BARGE_IN_MS,
        end_of_turn_ms: float = END_OF_TURN_MS,
        wake_window_ms: float = WAKE_WINDOW_MS,
        clock: Any = None,
    ) -> None:
        self.bus = bus
        self.barge_in_enabled = barge_in
        self.barge_in_ms = barge_in_ms
        self.end_of_turn_ms = end_of_turn_ms
        self.wake_window_ms = wake_window_ms
        self._clock = clock or (lambda: time.monotonic() * 1000)

        self.state = VoiceState.ASLEEP
        self.events: list[SessionEvent] = []

        self._speech_started_at: float | None = None
        self._silence_started_at: float | None = None
        self._listening_since: float | None = None
        self._turn_started_at: float | None = None
        self._transcript: list[str] = []
        self._trigger = Trigger.WAKE_WORD
        self.interruptions = 0
        self.spoken_cut_short = False

    # ------------------------------------------------------------------ helpers

    def _now(self) -> float:
        return float(self._clock())

    def _emit(self, kind: str, **detail: Any) -> SessionEvent:
        event = SessionEvent(kind, self.state, detail)
        self.events.append(event)
        if self.bus is not None:
            self.bus.emit(Topic.VOICE_STATE, state=self.state.value, kind=kind, **detail)
        return event

    def _enter(self, state: VoiceState, kind: str, **detail: Any) -> SessionEvent:
        self.state = state
        return self._emit(kind, **detail)

    # ------------------------------------------------------------------ opening

    def wake(self, trigger: Trigger = Trigger.WAKE_WORD) -> SessionEvent:
        """Something asked for GARIS's attention."""
        if self.state is VoiceState.PAUSED:
            return self._emit("ignored", reason="mikrofon wyłączony")

        # Waking while GARIS talks is itself an interruption: the user chose to
        # cut in, and honouring that is the whole point of push-to-talk.
        if self.state is VoiceState.SPEAKING:
            self.stop_speaking(reason="przerwane przez użytkownika")

        self._trigger = trigger
        self._listening_since = self._now()
        self._turn_started_at = None
        self._transcript = []
        self._speech_started_at = None
        self._silence_started_at = None
        return self._enter(VoiceState.LISTENING, "listening", trigger=trigger.value)

    def pause(self) -> SessionEvent:
        """User switched the microphone off."""
        return self._enter(VoiceState.PAUSED, "paused")

    def resume(self) -> SessionEvent:
        return self._enter(VoiceState.ASLEEP, "asleep")

    # ------------------------------------------------------------------- speech

    def speech_started(self) -> SessionEvent | None:
        """Voice activity detected. The one place barge-in happens."""
        now = self._now()

        if self.state is VoiceState.SPEAKING:
            if not self.barge_in_enabled:
                return self._emit("ignored", reason="przerywanie wyłączone")
            # Note the moment, but do not cut yet: a single sound is not a turn.
            self._speech_started_at = now
            return self._emit("maybe_interrupting")

        if self.state in (VoiceState.ASLEEP, VoiceState.PAUSED):
            return None

        self._speech_started_at = now
        self._silence_started_at = None
        if self._turn_started_at is None:
            self._turn_started_at = now
        return self._emit("speech")

    def audio_tick(self, *, speaking: bool) -> SessionEvent | None:
        """Called per audio frame with the VAD's verdict.

        Splitting this from ``speech_started`` is what makes barge-in trustworthy:
        the decision to cut GARIS off needs *sustained* speech, which only a
        stream of frames can establish.
        """
        now = self._now()

        if speaking:
            if self.state is VoiceState.SPEAKING and self.barge_in_enabled:
                if self._speech_started_at is None:
                    self._speech_started_at = now
                elif now - self._speech_started_at >= self.barge_in_ms:
                    self.interruptions += 1
                    self.stop_speaking(reason="przerwane przez użytkownika")
                    return self.wake(Trigger.FOLLOW_UP)
                return None

            if self.state is VoiceState.LISTENING:
                self._silence_started_at = None
                if self._turn_started_at is None:
                    self._turn_started_at = now
            return None

        # Silence.
        if self.state is VoiceState.SPEAKING:
            self._speech_started_at = None
            return None

        if self.state is not VoiceState.LISTENING:
            return None

        if self._silence_started_at is None:
            self._silence_started_at = now
            return None

        quiet_for = now - self._silence_started_at

        if self._turn_started_at is not None and quiet_for >= self.end_of_turn_ms:
            return self.end_turn()

        # Woken but nothing said: close the window instead of listening forever.
        if (
            self._turn_started_at is None
            and self._listening_since is not None
            and now - self._listening_since >= self.wake_window_ms
        ):
            return self._enter(VoiceState.ASLEEP, "timeout")

        return None

    def heard(self, text: str) -> SessionEvent:
        """Partial or final transcription from the engine."""
        cleaned = text.strip()
        if cleaned:
            self._transcript.append(cleaned)
        return self._emit("transcript", text=cleaned, partial=True)

    def end_turn(self) -> SessionEvent:
        """The user stopped talking; hand what they said to the agent."""
        text = " ".join(self._transcript).strip()
        turn = Turn(
            text=text,
            started_at=self._turn_started_at or self._now(),
            ended_at=self._now(),
            trigger=self._trigger,
        )
        self._transcript = []
        self._turn_started_at = None
        self._silence_started_at = None

        if not text:
            return self._enter(VoiceState.ASLEEP, "empty_turn")
        return self._enter(VoiceState.THINKING, "turn", text=text,
                           duration_ms=turn.duration)

    # ------------------------------------------------------------------ replying

    def start_speaking(self, text: str = "") -> SessionEvent:
        self._speech_started_at = None
        self.spoken_cut_short = False
        return self._enter(VoiceState.SPEAKING, "speaking", text=text)

    def stop_speaking(self, *, reason: str = "koniec wypowiedzi") -> SessionEvent:
        """Stop output now. Engines must treat this as "cut the audio", not "fade"."""
        if self.state is not VoiceState.SPEAKING:
            return self._emit("ignored", reason="nie mówię")
        self.spoken_cut_short = reason != "koniec wypowiedzi"
        return self._enter(VoiceState.ASLEEP, "stopped_speaking", reason=reason)

    def finished_speaking(self, *, keep_floor: bool = False) -> SessionEvent:
        """GARIS reached the end of its sentence on its own.

        ``keep_floor`` leaves the microphone open for a reply, which is what makes
        a conversation feel like a conversation instead of a series of commands.
        """
        if keep_floor:
            return self.wake(Trigger.FOLLOW_UP)
        return self._enter(VoiceState.ASLEEP, "done_speaking")

    # ---------------------------------------------------------------- inspection

    @property
    def listening(self) -> bool:
        return self.state is VoiceState.LISTENING

    @property
    def speaking(self) -> bool:
        return self.state is VoiceState.SPEAKING

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "barge_in": self.barge_in_enabled,
            "interruptions": self.interruptions,
            "transcript": " ".join(self._transcript),
            "trigger": self._trigger.value,
        }


__all__ = [
    "BARGE_IN_MS",
    "END_OF_TURN_MS",
    "WAKE_WINDOW_MS",
    "SessionEvent",
    "Trigger",
    "Turn",
    "VoiceSession",
    "VoiceState",
]
