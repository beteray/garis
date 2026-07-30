"""The wake word.

Two things must be true at once: it has to be always listening, and nothing may
leave the machine while it is. That rules out streaming audio to a provider for
wake detection, so this always runs locally — a small on-device model in
production, and the text matcher here for transcript-driven paths and tests.

The phrase is the user's to choose. "Garis" is a default, not a constant, and
speech recognition will mangle whatever they pick — so matching is fuzzy on
purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..text import fold

DEFAULT_PHRASE = "garis"

# What Polish and English recognisers actually produce for "garis". Being deaf to
# your own name because the recogniser heard "garys" is the difference between an
# assistant that works and one people give up on.
KNOWN_CONFUSIONS: dict[str, tuple[str, ...]] = {
    "garis": ("garis", "garys", "gariś", "charis", "haris", "garris", "gars",
              "garix", "garic", "gariz", "carys", "garuś"),
}


def normalise(text: str) -> str:
    """Lower-case, strip diacritics and punctuation — recognisers are inconsistent."""
    return fold(text)


def _distance(a: str, b: str, *, cap: int = 3) -> int:
    """Levenshtein distance, abandoned once it exceeds what we would accept."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ch_a != ch_b))
            )
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


@dataclass(slots=True)
class WakeMatch:
    matched: bool
    phrase: str = ""
    heard: str = ""
    remainder: str = ""       # what the user said *after* the wake word
    distance: int = 0

    def __bool__(self) -> bool:
        return self.matched


@dataclass
class WakeWordMatcher:
    """Fuzzy matcher for a chosen wake phrase.

    ``sensitivity`` 0..1 trades false wakes against missed ones. The default is
    deliberately forgiving: a missed wake word is a broken product, while a rare
    false wake costs a moment of the orb lighting up.
    """

    phrase: str = DEFAULT_PHRASE
    sensitivity: float = 0.6
    extra_variants: tuple[str, ...] = ()
    _variants: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.set_phrase(self.phrase)

    def set_phrase(self, phrase: str) -> None:
        """Change the wake word at runtime — settings must take effect immediately."""
        cleaned = normalise(phrase) or DEFAULT_PHRASE
        self.phrase = cleaned
        variants = {cleaned, *KNOWN_CONFUSIONS.get(cleaned, ())}
        variants |= {normalise(v) for v in self.extra_variants}
        self._variants = {v for v in variants if v}

    @property
    def tolerance(self) -> int:
        """Edit distance allowed, scaled by phrase length and sensitivity."""
        if len(self.phrase) <= 4:
            return 1 if self.sensitivity >= 0.5 else 0
        return 2 if self.sensitivity >= 0.75 else 1

    def match(self, heard: str) -> WakeMatch:
        words = normalise(heard).split()
        if not words:
            return WakeMatch(False)

        target_words = self.phrase.split()
        window = len(target_words)

        # Only the opening of the utterance counts. "Powiedz Garisowi, że…" is
        # about GARIS, not to it, and waking on it would be maddening.
        for start in range(min(len(words), 3)):
            candidate = " ".join(words[start : start + window])
            if not candidate:
                continue
            if candidate in self._variants:
                return WakeMatch(True, self.phrase, candidate,
                                 " ".join(words[start + window :]))
            for variant in self._variants:
                distance = _distance(candidate, variant, cap=self.tolerance)
                if distance <= self.tolerance:
                    return WakeMatch(True, self.phrase, candidate,
                                     " ".join(words[start + window :]), distance)
        return WakeMatch(False, self.phrase, " ".join(words[:window]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phrase": self.phrase,
            "sensitivity": self.sensitivity,
            "variants": sorted(self._variants),
            "tolerance": self.tolerance,
        }


class WakeWordDetector(Protocol):
    """An always-on, on-device detector.

    Implemented by openWakeWord in production. Audio handed here must never leave
    the machine — that is the whole reason this runs locally.
    """

    def feed(self, frame: bytes) -> bool:
        """Push one audio frame; True when the wake word just fired."""
        ...

    def set_phrase(self, phrase: str) -> None: ...

    @property
    def ready(self) -> bool:
        """False when the model is missing, so the UI can offer to fetch it."""
        ...


class TranscriptDetector:
    """Detector backed by a running transcript rather than raw audio.

    Used when speech is already being transcribed for another reason, and by the
    tests. Cheap, exact, and never a reason to ship audio anywhere.
    """

    def __init__(self, matcher: WakeWordMatcher | None = None) -> None:
        self.matcher = matcher or WakeWordMatcher()
        self.last: WakeMatch | None = None

    def feed_text(self, text: str) -> WakeMatch:
        self.last = self.matcher.match(text)
        return self.last

    def set_phrase(self, phrase: str) -> None:
        self.matcher.set_phrase(phrase)

    @property
    def ready(self) -> bool:
        return True


__all__ = [
    "DEFAULT_PHRASE",
    "KNOWN_CONFUSIONS",
    "TranscriptDetector",
    "WakeMatch",
    "WakeWordDetector",
    "WakeWordMatcher",
    "normalise",
]
