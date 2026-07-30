"""Text folding for matching.

One implementation, because two would drift. Memory search and the wake word both
need "does this roughly say the same thing", and both were getting Polish wrong in
the same way.

The trap: ``unicodedata.normalize("NFKD", …)`` splits *most* accented letters into
a base letter plus a combining mark, so stripping combining marks folds them. But
some letters are not composed that way at all — Polish ``ł`` is one codepoint with
no decomposition, and so are ``ø``, ``đ``, ``ß``, ``æ``. NFKD leaves them intact,
and a naive "keep only a-z" pass then turns them into spaces, splitting words in
half. ``wołam`` became ``wo am``.
"""

from __future__ import annotations

import re
import unicodedata

# Letters that NFKD will not decompose, mapped by hand.
_INDECOMPOSABLE = str.maketrans(
    {
        "ł": "l", "Ł": "L",
        "ø": "o", "Ø": "O",
        "đ": "d", "Đ": "D",
        "ð": "d", "Ð": "D",
        "þ": "th", "Þ": "Th",
        "ß": "ss",
        "æ": "ae", "Æ": "Ae",
        "œ": "oe", "Œ": "Oe",
        "ı": "i", "İ": "I",  # noqa: RUF001 - folding the dotless i is the point
    }
)

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Lower-case, strip accents, keep letters and digits.

    Used wherever two pieces of human text have to be compared without caring
    about case, accents or punctuation.
    """
    lowered = text.lower().translate(_INDECOMPOSABLE)
    decomposed = unicodedata.normalize("NFKD", lowered)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", _NON_WORD.sub(" ", without_marks)).strip()


def fold_keep_shape(text: str) -> str:
    """Fold accents but keep punctuation — for substring search in longer text."""
    lowered = text.lower().translate(_INDECOMPOSABLE)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


__all__ = ["fold", "fold_keep_shape"]
