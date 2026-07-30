"""Which notices actually reach a person.

The product rule: GARIS is silent by default and interrupts rarely. These tests
are what stop that promise from eroding one "useful notification" at a time.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from garis.config import NotificationsConfig, QuietHours
from garis.notifications import CRITICAL, Notice, NotificationGate, Verdict
from garis.text import fold, fold_keep_shape


def at(hour: int, minute: int = 0):
    return lambda: datetime(2026, 7, 30, hour, minute)


def gate(config: NotificationsConfig | None = None, hour: int = 14, bus=None):
    return NotificationGate(config or NotificationsConfig(), bus=bus, clock=at(hour))


# ------------------------------------------------------------------ importance


def test_trivia_never_reaches_the_user():
    decision = gate().consider(Notice("Zaktualizowano indeks", importance=1))
    assert decision.verdict is Verdict.DROP


def test_worth_knowing_gets_through_during_the_day():
    decision = gate().consider(Notice("Kopia zapasowa zrobiona", importance=3))
    assert decision.delivered


def test_the_threshold_is_configurable():
    strict = gate(NotificationsConfig(min_importance=4))
    assert not strict.consider(Notice("Coś tam", importance=3)).delivered
    assert strict.consider(Notice("Ważne", importance=4)).delivered


# ----------------------------------------------------------------- quiet hours


def test_quiet_hours_hold_rather_than_drop():
    """Held, not lost — the user still finds out, just not at 03:00."""
    night = gate(hour=3)
    decision = night.consider(Notice("Serwer wrócił", importance=3))

    assert decision.verdict is Verdict.HOLD
    assert len(night.held) == 1


def test_something_critical_wakes_you_anyway():
    night = gate(hour=3)
    decision = night.consider(Notice("Dysk systemowy jest pełny", importance=CRITICAL))
    assert decision.delivered and decision.reason == "krytyczne"


def test_held_notices_come_back_most_important_first():
    night = gate(hour=3)
    night.consider(Notice("Drobiazg", importance=3, source="a"))
    night.consider(Notice("Poważniejsze", importance=4, source="b"))

    released = night.release_held()
    assert [n.message for n in released] == ["Poważniejsze", "Drobiazg"]
    assert night.held == []


def test_quiet_hours_can_cross_midnight():
    config = NotificationsConfig(quiet_hours=QuietHours(start="23:00", end="08:00"))
    assert gate(config, hour=2).consider(Notice("x", importance=3)).verdict is Verdict.HOLD
    assert gate(config, hour=23).consider(Notice("x", importance=3)).verdict is Verdict.HOLD
    assert gate(config, hour=12).consider(Notice("x", importance=3)).delivered


def test_quiet_hours_can_be_switched_off():
    config = NotificationsConfig(quiet_hours=QuietHours(enabled=False))
    assert gate(config, hour=3).consider(Notice("x", importance=3)).delivered


# --------------------------------------------------------------------- gaming


def test_a_game_is_not_interrupted():
    playing = gate()
    playing.set_gaming(True)
    decision = playing.consider(Notice("Zadanie skończone", importance=3))
    assert decision.verdict is Verdict.HOLD and decision.reason == "gra"


def test_a_game_does_not_shield_you_from_something_critical():
    playing = gate()
    playing.set_gaming(True)
    assert playing.consider(Notice("Serwer padł", importance=CRITICAL)).delivered


def test_closing_the_game_releases_what_was_held():
    playing = gate()
    playing.set_gaming(True)
    playing.consider(Notice("Gotowe", importance=3))
    playing.set_gaming(False)
    assert len(playing.release_held()) == 1


# ------------------------------------------------------- duplicates and volume


def test_the_same_notice_twice_is_one_notice():
    """Ten notices about one failing service is one notice."""
    single = gate()
    assert single.consider(Notice("Usługa nie odpowiada", importance=4, source="watch")).delivered
    second = single.consider(Notice("Usługa nie odpowiada", importance=4, source="watch"))
    assert second.verdict is Verdict.DROP and second.reason == "duplikat"


def test_the_same_words_from_a_different_source_are_not_duplicates():
    single = gate()
    single.consider(Notice("Nie odpowiada", importance=4, source="serwer-1"))
    assert single.consider(Notice("Nie odpowiada", importance=4, source="serwer-2")).delivered


def test_a_flood_is_held_after_the_hourly_cap():
    limited = gate(NotificationsConfig(max_per_hour=3))
    for index in range(3):
        assert limited.consider(Notice(f"Zdarzenie {index}", importance=3)).delivered
    overflow = limited.consider(Notice("Zdarzenie 4", importance=3))
    assert overflow.verdict is Verdict.HOLD and "limit" in overflow.reason


def test_duplicates_are_suppressed_while_held_too():
    """Otherwise a service failing every minute at night becomes 480 morning pings."""
    night = gate(hour=3)
    night.consider(Notice("Nie odpowiada", importance=3, source="watch"))
    night.consider(Notice("Nie odpowiada", importance=3, source="watch"))
    assert len(night.held) == 1


# --------------------------------------------------------------------- wiring


def test_delivering_emits_speech_for_the_voice_layer(bus):
    speaking = gate(bus=bus)
    speaking.consider(Notice("Gotowe", importance=3, task_id="t1"))

    spoken = bus.recent("speak")
    assert spoken and spoken[-1].payload["message"] == "Gotowe"
    assert spoken[-1].payload["task_id"] == "t1"


def test_holding_says_nothing_out_loud(bus):
    night = gate(hour=3, bus=bus)
    night.consider(Notice("Później", importance=3))
    assert bus.recent("speak") == []


def test_describe_answers_why_it_is_quiet():
    night = gate(hour=3)
    night.consider(Notice("x", importance=3))
    described = night.describe()
    assert described["quiet_hours"]["active_now"] is True
    assert described["held"] == 1


# ----------------------------------------------------------------- text folding


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Zażółć GĘŚLĄ jaźń!", "zazolc gesla jazn"),
        ("wołam", "wolam"),
        ("Miłosz Wójcik", "milosz wojcik"),
        ("Straße", "strasse"),
        ("Ærø", "aero"),
    ],
)
def test_folding_handles_letters_that_nfkd_will_not_decompose(raw: str, expected: str):
    """`ł` is one codepoint with no decomposition; NFKD alone leaves it, and a
    naive a-z filter then turns it into a space, splitting the word in half."""
    assert fold(raw) == expected


def test_folding_that_keeps_punctuation_still_folds_the_letters():
    assert fold_keep_shape("Wołam: hasło!") == "wolam: haslo!"
