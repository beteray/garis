"""Which machinery actually turns speech into words and back.

Two roads, and the user picks neither:

* **Provider realtime** — Gemini Live, OpenAI Realtime. One speech-to-speech
  session with interruption handled at the source: the lowest latency and the
  most natural turn-taking available.
* **Local** — faster-whisper for hearing, Piper for speaking. Free, private,
  works with no network.

The router chooses, on the same grounds it chooses a model: what the work needs,
what is available, and how private it has to be. ``local_only`` never reaches for
a provider, and no network means local, so a dropped connection changes the
quality of the conversation rather than ending it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from ..models import Capability, Job, ModelRouter, Need, Privacy


class Road(StrEnum):
    PROVIDER_REALTIME = "provider_realtime"
    LOCAL = "local"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class VoicePlan:
    """How this conversation will be carried, and why."""

    road: Road
    provider: str = ""
    model: str = ""
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.road is not Road.UNAVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "road": self.road.value,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
        }


class SpeechToText(Protocol):
    """Streaming transcription."""

    async def start(self, *, sample_rate: int = 16_000, language: str = "pl") -> None: ...

    async def feed(self, frame: bytes) -> str | None:
        """Push audio; return a partial transcript when there is one."""
        ...

    async def finish(self) -> str:
        """Close the utterance and return the final transcript."""
        ...

    @property
    def ready(self) -> bool: ...


class TextToSpeech(Protocol):
    """Streaming synthesis. Must be interruptible mid-sentence."""

    def synthesise(self, text: str, *, voice: str = "") -> AsyncIterator[bytes]: ...

    async def stop(self) -> None:
        """Cut output immediately — barge-in depends on this being instant."""
        ...

    def voices(self) -> Sequence[str]: ...

    @property
    def ready(self) -> bool: ...


class RealtimeVoice(Protocol):
    """A provider's speech-to-speech session."""

    async def connect(self, *, voice: str = "", language: str = "pl") -> None: ...

    async def send_audio(self, frame: bytes) -> None: ...

    def events(self) -> AsyncIterator[dict[str, Any]]:
        """Transcripts, audio and turn boundaries as the provider reports them."""
        ...

    async def interrupt(self) -> None:
        """Tell the provider the user cut in, so it stops generating."""
        ...

    async def close(self) -> None: ...


def choose_road(
    router: ModelRouter,
    *,
    privacy: Privacy = Privacy.ANY,
    prefer: str = "auto",
    online: bool = True,
    local_ready: bool = False,
) -> VoicePlan:
    """Pick the road for this conversation.

    ``prefer`` mirrors ``config.voice.engine`` so a user who insists can pin it,
    but the default is auto and the default is what almost everyone runs.
    """
    if prefer == "local" or privacy is Privacy.LOCAL_ONLY:
        if local_ready:
            return VoicePlan(Road.LOCAL, provider="local",
                             reason="prywatność: nic nie opuszcza komputera")
        return VoicePlan(
            Road.UNAVAILABLE,
            reason="tryb lokalny wymaga pobrania silników mowy",
        )

    if online and prefer in ("auto", "provider_realtime"):
        candidates = router.candidates(
            Need(job=Job.VOICE, privacy=privacy,
                 requires=frozenset({Capability.REALTIME_VOICE}))
        )
        if candidates:
            best = candidates[0]
            return VoicePlan(
                Road.PROVIDER_REALTIME,
                provider=best.model.provider,
                model=best.model.name,
                reason="rozmowa w czasie rzeczywistym u dostawcy",
            )
        if prefer == "provider_realtime":
            return VoicePlan(Road.UNAVAILABLE,
                             reason="żaden skonfigurowany dostawca nie ma trybu realtime")

    if local_ready:
        return VoicePlan(
            Road.LOCAL,
            provider="local",
            reason="brak sieci" if not online else "brak dostawcy realtime",
        )

    return VoicePlan(
        Road.UNAVAILABLE,
        reason="brak silnika mowy — dodaj klucz dostawcy albo pobierz silniki lokalne",
    )


# --------------------------------------------------------------------- doubles


class ScriptedSpeechToText:
    """Deterministic STT for tests and for driving the UI without a microphone."""

    def __init__(self, transcripts: Sequence[str] = ()) -> None:
        self._queue = list(transcripts)
        self._heard: list[str] = []
        self.started = False

    async def start(self, *, sample_rate: int = 16_000, language: str = "pl") -> None:
        self.started = True

    async def feed(self, frame: bytes) -> str | None:
        del frame
        if not self._queue:
            return None
        piece = self._queue.pop(0)
        self._heard.append(piece)
        return piece

    async def finish(self) -> str:
        text = " ".join(self._heard).strip()
        self._heard.clear()
        self.started = False
        return text

    @property
    def ready(self) -> bool:
        return True


class RecordingTextToSpeech:
    """TTS double that records what it was asked to say and whether it was cut off."""

    def __init__(self) -> None:
        self.said: list[str] = []
        self.stopped = 0
        self.interrupted_text: str | None = None
        self._speaking: str | None = None

    async def _stream(self, text: str) -> AsyncIterator[bytes]:
        self._speaking = text
        for word in text.split():
            yield word.encode("utf-8")
        self._speaking = None
        self.said.append(text)

    def synthesise(self, text: str, *, voice: str = "") -> AsyncIterator[bytes]:
        del voice
        return self._stream(text)

    async def stop(self) -> None:
        self.stopped += 1
        if self._speaking is not None:
            self.interrupted_text = self._speaking
            self._speaking = None

    def voices(self) -> Sequence[str]:
        return ("pl_female_calm", "pl_male_neutral")

    @property
    def ready(self) -> bool:
        return True


__all__ = [
    "RealtimeVoice",
    "RecordingTextToSpeech",
    "Road",
    "ScriptedSpeechToText",
    "SpeechToText",
    "TextToSpeech",
    "VoicePlan",
    "choose_road",
]
