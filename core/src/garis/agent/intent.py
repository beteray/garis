"""Deciding whether something said to GARIS is work or conversation.

The measured failure: everything typed into the window became a durable task.
"cześć" was planned, failed to plan, and stayed in the task list as `blocked`
forever — a greeting with a progress bar. Worse, with no provider configured the
greeting could not even be answered, because answering it also went through the
planner.

So the first pass is deterministic and cannot need a model. Greetings, thanks,
one-word acknowledgements, "co potrafisz" and plain arithmetic are decided here,
by rules, and are answered without a task existing. Anything this file is not
sure about is handed on: the rule is **a task exists only when there is
something to track**, and "I am not sure" is not proof there is nothing.

The classifier deliberately refuses to be clever. A sentence that merely opens
with "cześć" and then asks for work is work — the greeting is stripped and what
is left decides.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import StrEnum

from ..text import fold


class Intent(StrEnum):
    """What the person meant. The product states in ``docs/STATE_MACHINES.md`` §4."""

    GREETING = "greeting"
    SMALL_TALK = "small_talk"
    THANKS = "thanks"
    FAREWELL = "farewell"
    ACKNOWLEDGEMENT = "acknowledgement"
    ABOUT_SELF = "about_self"
    CAPABILITIES = "capabilities"
    STATUS = "status"
    ARITHMETIC = "arithmetic"
    TASK = "task"
    UNSURE = "unsure"

    @property
    def is_conversation(self) -> bool:
        """True when answering is the whole job and no task should be created."""
        return self not in (Intent.TASK, Intent.UNSURE)


@dataclass(frozen=True, slots=True)
class Verdict:
    intent: Intent
    reason: str                 # why, for the diagnostic view — never shown by default
    answer: str = ""            # a computed fact (arithmetic), not a generated sentence
    remainder: str = ""         # what was left after stripping a leading greeting


# Folded (accent-free, lower-case) forms — ``fold`` turns "Cześć!" into "czesc".
_GREETING = {
    "czesc", "hej", "hejka", "hej garis", "siema", "siemano", "elo", "witaj",
    "witam", "dzien dobry", "dobry wieczor", "dobry", "halo", "hello", "hi",
    "hey", "yo", "no hej", "no czesc", "garis",
}
_FAREWELL = {
    "pa", "papa", "na razie", "narazie", "nara", "do zobaczenia", "do jutra",
    "dobranoc", "bye", "goodbye", "koniec na dzis", "trzymaj sie",
}
_THANKS = {
    "dzieki", "dziekuje", "dziekuje ci", "wielkie dzieki", "dzieki wielkie",
    "dziex", "thx", "thanks", "thank you", "dzieki za pomoc", "super dzieki",
}
_ACK = {
    "ok", "okej", "okey", "oki", "spoko", "jasne", "rozumiem", "dobra", "dobrze",
    "git", "aha", "no", "yhm", "mhm", "tak", "nie", "zgoda", "swietnie", "super",
    "no dobra", "w porzadku",
}
_SMALL_TALK = {
    "jak sie masz", "jak leci", "co slychac", "co u ciebie", "jak tam",
    "wszystko ok", "wszystko w porzadku", "jak sie czujesz", "how are you",
    "co robisz", "spisz", "jestes tam", "jestes", "zyjesz",
}
_ABOUT_SELF = {
    "kim jestes", "kto ty jestes", "co ty jestes", "jak masz na imie",
    "jak sie nazywasz", "kto cie stworzyl", "kto cie zrobil", "przedstaw sie",
    "powiedz cos o sobie", "who are you",
}
_CAPABILITIES = {
    "co potrafisz", "co umiesz", "co mozesz zrobic", "co mozesz", "pomoc",
    "help", "w czym mozesz pomoc", "jakie masz funkcje", "co ty w ogole robisz",
    "do czego sluzysz", "jakie masz narzedzia",
}
_STATUS = {
    "co robisz teraz", "nad czym pracujesz", "co masz do zrobienia",
    "jak idzie", "status", "co sie dzieje", "czym sie zajmujesz",
}

# A greeting glued to the front of real work: "cześć, sprawdź dysk".
_LEADING_GREETING = re.compile(
    r"^\s*(?:hej|hejka|cze[śs][ćc]|siema|siemano|elo|witaj|witam|halo|hello|hi|yo|"
    r"dzie[ńn]\s+dobry|dobry\s+wiecz[óo]r|garis)\b[\s,.!—-]*",
    re.IGNORECASE,
)

# "ile to 2+2", "policz 15 * 3", "2+2" — the words are optional scaffolding.
_ARITHMETIC_LEAD = re.compile(
    r"^\s*(?:ile\s+(?:to|jest|wynosi)|policz|oblicz|licz|how\s+much\s+is|"
    r"what\s+is|wylicz)\b[\s:]*",
    re.IGNORECASE,
)
_ARITHMETIC_BODY = re.compile(r"^[\d\s+\-*/().,^]+$")
_DATE_LIKE = re.compile(r"^\d{2,4}-\d{1,2}-\d{1,4}$")

# Words that mean "there is something to do", so no chat rule may claim the text.
_WORK = re.compile(
    r"\b(sprawd[źz]|zr[óo]b|zrobisz|wykonaj|uruchom|otw[óo]rz|zamknij|znajd[źz]|"
    r"poszukaj|wyszukaj|pobierz|[śs]ci[ąa]gnij|zapisz|usu[ńn]|skasuj|przenie[śs]|"
    r"skopiuj|wy[śs]lij|napisz|utw[óo]rz|stw[óo]rz|zainstaluj|odinstaluj|"
    r"posprz[ąa]taj|uporz[ąa]dkuj|przypomnij|zaplanuj|ustaw|w[łl][ąa]cz|wy[łl][ąa]cz|"
    r"zaktualizuj|napraw|przeanalizuj|streszcz|przet[łl]umacz|zmie[ńn]|dodaj|"
    r"przygotuj|dopisz|podsumuj|wypisz|poka[żz]|zbierz|sprawd[źz]my|zarezerwuj|"
    r"um[óo]w|odpowiedz|przypilnuj|obserwuj|monitoruj|policz\s+\w*[a-ząćęłńóśźż]{3})\b",
    re.IGNORECASE,
)

_MAX_CHAT_WORDS = 7


def classify(text: str) -> Verdict:
    """Decide without a model. Never guesses "conversation" when work is named."""
    stripped = text.strip()
    if not stripped:
        return Verdict(Intent.UNSURE, "pusta wiadomość")

    # A greeting in front of a request is punctuation, not the message.
    remainder = _LEADING_GREETING.sub("", stripped).strip(" ,.!?—-")
    if remainder and remainder != stripped:
        inner = classify(remainder)
        if inner.intent is not Intent.UNSURE:
            return Verdict(inner.intent, f"po powitaniu: {inner.reason}",
                           inner.answer, remainder)
        return Verdict(Intent.UNSURE, "powitanie plus treść", remainder=remainder)

    answer = _arithmetic(stripped)
    if answer is not None:
        return Verdict(Intent.ARITHMETIC, "wyrażenie arytmetyczne", answer)

    if _WORK.search(stripped):
        return Verdict(Intent.TASK, "czasownik wykonawczy")

    folded = fold(stripped)
    if not folded:
        return Verdict(Intent.UNSURE, "sama interpunkcja")

    # Only short utterances can be small talk; a paragraph that happens to
    # contain "dzięki" is not a thank-you note.
    if len(folded.split()) <= _MAX_CHAT_WORDS:
        for table, intent in (
            (_GREETING, Intent.GREETING),
            (_FAREWELL, Intent.FAREWELL),
            (_THANKS, Intent.THANKS),
            (_ACK, Intent.ACKNOWLEDGEMENT),
            (_SMALL_TALK, Intent.SMALL_TALK),
            (_ABOUT_SELF, Intent.ABOUT_SELF),
            (_CAPABILITIES, Intent.CAPABILITIES),
            (_STATUS, Intent.STATUS),
        ):
            if folded in table:
                return Verdict(intent, "dopasowanie dokładne")

    return Verdict(Intent.UNSURE, "brak reguły")


# ------------------------------------------------------------------ arithmetic

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.FloorDiv, ast.USub, ast.UAdd,
)


def _arithmetic(text: str) -> str | None:
    """Evaluate a plain sum, or return None. Never runs anything but arithmetic."""
    body = _ARITHMETIC_LEAD.sub("", text).strip().rstrip("=?.! ")
    if not body or not _ARITHMETIC_BODY.match(body):
        return None
    if not any(op in body for op in "+-*/^"):
        return None  # a bare number is not a question
    if _DATE_LIKE.match(body):
        return None  # "2026-08-02" is a date, and answering "2016" is absurd

    # Polish writes decimals with a comma; "2,5" is one number, not two.
    normalised = re.sub(r"(?<=\d),(?=\d)", ".", body).replace("^", "**")
    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None
        if isinstance(node, ast.Constant) and not isinstance(node.value, int | float):
            return None
    try:
        # The tree above contains nothing but literal arithmetic.
        value = eval(
            compile(tree, "<arithmetic>", "eval"), {"__builtins__": {}}, {}
        )
    except ZeroDivisionError:
        return "Przez zero nie dzielimy."
    except (ArithmeticError, ValueError, TypeError):
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        if value.is_integer():
            return str(int(value))
        return f"{value:.10g}".replace(".", ",")
    return str(value)


__all__ = ["Intent", "Verdict", "classify"]
