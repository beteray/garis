"""The disk numbers have to agree with each other.

The acceptance run that prompted this file reported, all at once:

    total 252 GB · free 24,1 GB · used 5%

Every figure was measured and none of them was wrong on its own. `free` was the
space available to this user, `5%` was blocks in use over total, and 215 GB sat
between them as a container quota that nothing mentioned. A person reading that
sentence cannot reconcile 24 GB left with 5% used, and an interface that asks
them to is lying by arrangement even when every number is true.

So: one subtraction defines the user-facing pair, the parts add up to the whole,
and anything that does not follow is refused rather than presented.
"""

from __future__ import annotations

import os
import sys

import pytest

from garis.agent import reflex
from garis.kernel import ToolTarget
from garis.tools.system import _disk_bytes, _memory_bytes


def test_the_measured_set_is_internally_consistent() -> None:
    value = _disk_bytes("/" if sys.platform != "win32" else "C:\\")

    assert value["total"] > 0
    assert 0 <= value["free"] <= value["total"]
    # The pair the sentence is built from.
    assert value["used"] == value["total"] - value["free"]
    assert abs(value["percent_used"] - value["used"] / value["total"] * 100) < 0.1
    # And the split of "used" into files versus space withheld from us.
    assert value["filesystem_used"] + value["reserved"] == value["used"]
    assert value["filesystem_free"] >= value["free"]


def test_every_measured_disk_passes_its_own_validator(tmp_path) -> None:
    for target in ("/" if sys.platform != "win32" else "C:\\", str(tmp_path)):
        value = _disk_bytes(target)
        assert reflex.check(ToolTarget("disk_usage"), value) == "", target


def test_a_percentage_that_contradicts_the_free_space_is_refused() -> None:
    """The exact shape of the bug: free and percent describing different things."""
    total = 252 * 1024**3
    free = 24 * 1024**3
    contradictory = {
        "path": "/",
        "total": total,
        "free": free,
        "used": total - free,
        "filesystem_used": total - free,
        "reserved": 0,
        "percent_used": 5.1,          # from blocks-in-use, not from `free`
    }
    why = reflex.check(ToolTarget("disk_usage"), contradictory)
    assert "procent" in why
    assert reflex.answer(ToolTarget("disk_usage"), contradictory) == ""


def test_parts_that_do_not_add_up_are_refused() -> None:
    total = 100 * 1024**3
    free = 40 * 1024**3
    assert reflex.check(ToolTarget("disk_usage"), {
        "total": total, "free": free, "used": 10 * 1024**3,
    }) == "zajęte + wolne nie sumuje się do pojemności"

    assert reflex.check(ToolTarget("disk_usage"), {
        "total": total, "free": free, "used": total - free,
        "filesystem_used": 1024, "reserved": 1024,
    }) == "zajęte przez pliki + zarezerwowane nie sumuje się do zajętych"


def test_small_nonsense_is_not_saved_by_the_rounding_allowance() -> None:
    """A megabyte of slack must not swallow a ten-byte disk with 99 bytes free."""
    assert reflex.check(ToolTarget("disk_usage"), {"total": 10, "free": 99})


def test_the_sentence_explains_space_that_is_free_but_not_available() -> None:
    total = 252 * 1024**3
    free = 24 * 1024**3
    files = 13 * 1024**3
    value = {
        "path": "/",
        "total": total,
        "free": free,
        "filesystem_free": total - files,
        "filesystem_used": files,
        "reserved": (total - free) - files,
        "used": total - free,
        "percent_used": round((total - free) / total * 100, 1),
    }
    assert reflex.check(ToolTarget("disk_usage"), value) == ""
    sentence = reflex.answer(ToolTarget("disk_usage"), value)

    assert "24,0 GB" in sentence and "252,0 GB" in sentence and "90%" in sentence
    # And the 215 GB that made the original reading unreadable is named.
    assert "13,0 GB" in sentence and "215,0 GB" in sentence
    assert "niedostępne" in sentence


def test_a_disk_with_no_quota_says_nothing_about_reservations() -> None:
    total = 500 * 1024**3
    free = 100 * 1024**3
    value = {"path": "D:\\", "total": total, "free": free, "used": total - free,
             "filesystem_used": total - free, "reserved": 0,
             "percent_used": round((total - free) / total * 100, 1)}
    sentence = reflex.answer(ToolTarget("disk_usage"), value)
    assert "niedostępne" not in sentence
    assert sentence == "Na D:\\ zostało 100,0 GB z 500,0 GB — zajęte 80%."


def test_memory_numbers_are_consistent_too() -> None:
    value = _memory_bytes()
    if not value:                       # a host that will not tell us
        pytest.skip("brak psutil i /proc/meminfo")
    assert reflex.check(ToolTarget("memory_usage"), value) == ""
    assert value["used"] == value["total"] - value["available"]
    assert abs(value["percent_used"] - value["used"] / value["total"] * 100) < 0.6


@pytest.mark.skipif(sys.platform != "win32", reason="ścieżka windowsowa")
def test_windows_reads_the_quota_aware_numbers() -> None:  # pragma: no cover - Windows
    """`GetDiskFreeSpaceExW` distinguishes free-to-caller from free-on-volume.

    That distinction is the whole reason the Linux reading was confusing, and
    Windows has it natively: a per-user quota shows up as `reserved` here rather
    than silently skewing the percentage.
    """
    value = _disk_bytes("C:\\")
    assert value["total"] > 0
    assert value["free"] <= value["filesystem_free"]
    assert reflex.check(ToolTarget("disk_usage"), value) == ""


@pytest.mark.skipif(sys.platform == "win32", reason="ścieżka POSIX-owa")
def test_posix_reads_statvfs_directly() -> None:
    """f_bavail, not f_bfree: what this user may write, not what is unallocated."""
    stat = os.statvfs("/")
    block = stat.f_frsize or stat.f_bsize
    value = _disk_bytes("/")
    assert value["free"] == stat.f_bavail * block
    assert value["filesystem_free"] == stat.f_bfree * block
