"""A capability may not claim more than its evidence supports.

The layer exists because "the executor returned" and "the goal was achieved" are
different statements, and 0.1.2 could not tell them apart. These tests hold the
distinction at the level where it is now enforced: registration requires a
verifier, running goes through `perform`, and `verified` is computed in exactly
one place from two facts — a verifier ran, and it said yes.
"""

from __future__ import annotations

import sys

import pytest

from garis.capabilities import (
    REGISTRY,
    Capability,
    CapabilityRegistry,
    Field,
    Invocation,
    Outcome,
    Permission,
    Risk,
    always_unchecked,
    evidence_verifier,
)
from garis.capabilities.processes import is_running
from garis.errors import Unsupported


def _capability(**over) -> Capability:
    async def run(_: Invocation) -> Outcome:
        return Outcome(ok=True, value={"done": True}, evidence={"seen": 1})

    base = {
        "id": "test.thing",
        "version": 1,
        "summary": "Robi rzecz.",
        "risk": Risk.READ,
        "permission": Permission.NONE,
        "executor": run,
        "verifier": evidence_verifier(),
        "evidence": (Field("seen", "int", "Co zmierzono"),),
    }
    return Capability(**{**base, **over})


# ------------------------------------------------------------- the catalogue


async def test_a_capability_runs_and_reports_what_it_proved() -> None:
    registry = CapabilityRegistry()
    registry.add(_capability())
    # `evidence_verifier` looks the capability up in the shared registry, so the
    # local one has to be findable there too.
    REGISTRY.add(_capability())

    performed = await registry.perform("test.thing")

    assert performed.ok and performed.verified
    assert performed.verdict.checked
    assert performed.to_dict()["evidence"] == {"seen": 1}


async def test_missing_evidence_is_not_a_verified_result() -> None:
    async def run(_: Invocation) -> Outcome:
        return Outcome(ok=True, value={"done": True}, evidence={})   # nothing measured

    registry = CapabilityRegistry()
    registry.add(_capability(id="test.silent", executor=run))
    REGISTRY.add(_capability(id="test.silent", executor=run))

    performed = await registry.perform("test.silent")

    assert performed.ok, "wykonanie się powiodło"
    assert not performed.verified, "ale nic tego nie potwierdziło"
    assert performed.verdict.missing == ("seen",)


async def test_a_capability_with_no_postcondition_says_so() -> None:
    registry = CapabilityRegistry()
    registry.add(_capability(id="test.unchecked", verifier=always_unchecked, evidence=()))

    performed = await registry.perform("test.unchecked")

    assert performed.ok
    assert not performed.verified
    assert not performed.verdict.checked


async def test_evidence_that_fails_its_own_check_is_refused() -> None:
    async def run(_: Invocation) -> Outcome:
        return Outcome(ok=True, evidence={"scanned": 0})

    spec = _capability(
        id="test.zero",
        executor=run,
        evidence=(Field("scanned", "int", "Ile odczytano",
                        check=lambda v: isinstance(v, int) and v > 0),),
    )
    registry = CapabilityRegistry()
    registry.add(spec)
    REGISTRY.add(spec)

    performed = await registry.perform("test.zero")
    assert not performed.verified
    assert performed.verdict.missing == ("scanned",)


async def test_an_executor_that_crashes_produces_neither_success_nor_silence() -> None:
    async def boom(_: Invocation) -> Outcome:
        raise RuntimeError("bum")

    spec = _capability(id="test.boom", executor=boom, risk=Risk.REVERSIBLE)
    registry = CapabilityRegistry()
    registry.add(spec)
    REGISTRY.add(spec)

    performed = await registry.perform("test.boom")

    assert not performed.ok and not performed.verified
    assert "bum" in performed.outcome.error
    # It changed something or it did not; nobody knows, and that is recorded.
    assert performed.outcome.uncertain


async def test_an_unsupported_platform_is_answered_before_anything_runs() -> None:
    ran = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        ran["count"] += 1
        return Outcome(ok=True)

    other = "linux" if sys.platform == "win32" else "win32"
    spec = _capability(id="test.elsewhere", executor=run, platforms=(other,))
    registry = CapabilityRegistry()
    registry.add(spec)

    performed = await registry.perform("test.elsewhere")

    assert not performed.ok and not performed.verified
    assert ran["count"] == 0, "nie wolno uruchamiać czegoś, co tu nie działa"
    assert "nie działa na tym systemie" in performed.outcome.error


async def test_bad_arguments_stop_before_the_executor() -> None:
    ran = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        ran["count"] += 1
        return Outcome(ok=True, evidence={"seen": 1})

    spec = _capability(
        id="test.args",
        executor=run,
        inputs=(Field("level", "int", "Poziom", required=True),),
    )
    registry = CapabilityRegistry()
    registry.add(spec)

    performed = await registry.perform("test.args", {})
    assert not performed.ok and ran["count"] == 0
    assert "brakuje parametru" in performed.outcome.error


async def test_unknown_capabilities_are_not_invented() -> None:
    with pytest.raises(Unsupported):
        await CapabilityRegistry().perform("windows.nothing.here")


def test_the_planner_menu_shows_only_what_is_exposed_and_supported() -> None:
    registry = CapabilityRegistry()
    registry.add(_capability(id="test.hidden", exposed=False))
    registry.add(_capability(id="test.shown", exposed=True))
    other = "linux" if sys.platform == "win32" else "win32"
    registry.add(_capability(id="test.foreign", exposed=True, platforms=(other,)))

    menu = {row["id"] for row in registry.catalogue()}
    assert menu == {"test.shown"}


def test_a_second_version_cannot_squat_on_a_registered_id() -> None:
    registry = CapabilityRegistry()
    registry.add(_capability())
    with pytest.raises(ValueError):
        registry.add(_capability(version=2))


# ------------------------------------------------------------- effect identity


async def test_the_same_effect_is_not_performed_twice() -> None:
    """A resume after a crash must not repeat a real change."""
    runs = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        runs["count"] += 1
        return Outcome(ok=True, value={"run": runs["count"]}, evidence={"seen": 1})

    spec = _capability(id="test.once", executor=run, risk=Risk.IRREVERSIBLE)
    registry = CapabilityRegistry()
    registry.add(spec)
    REGISTRY.add(spec)

    first = await registry.perform("test.once", effect_id="launch-discord")
    second = await registry.perform("test.once", effect_id="launch-discord")

    assert runs["count"] == 1, "drugi przebieg wykonałby efekt jeszcze raz"
    assert second.outcome.value == first.outcome.value


async def test_a_failed_effect_may_be_attempted_again() -> None:
    """Refusing to retry a failure would strand the task; only success is final."""
    runs = {"count": 0}

    async def flaky(_: Invocation) -> Outcome:
        runs["count"] += 1
        ok = runs["count"] > 1
        return Outcome(ok=ok, evidence={"seen": 1} if ok else {},
                       error="" if ok else "nie tym razem")

    spec = _capability(id="test.flaky", executor=flaky)
    registry = CapabilityRegistry()
    registry.add(spec)
    REGISTRY.add(spec)

    assert not (await registry.perform("test.flaky", effect_id="e1")).ok
    assert (await registry.perform("test.flaky", effect_id="e1")).ok
    assert runs["count"] == 2


# ---------------------------------------------------------------- processes


async def test_the_process_list_measures_this_machine() -> None:
    performed = await REGISTRY.perform("windows.process.list", {"limit": 500})

    assert performed.ok and performed.verified
    assert performed.outcome.evidence["scanned"] > 0
    names = [row["name"] for row in performed.outcome.value["processes"]]
    assert names, "żaden proces nie został odczytany"
    # This test is itself a running Python process; if the reading were invented
    # it would have no reason to contain one.
    assert any("python" in name.lower() for name in names), names[:20]


async def test_a_named_process_is_only_claimed_when_it_was_seen() -> None:
    performed = await REGISTRY.perform(
        "windows.process.list", {"name": "nie-ma-takiego-programu-2026"}
    )

    assert performed.ok, "odczyt się udał"
    assert performed.outcome.evidence["found"] is False
    assert performed.outcome.value["matched"] == 0
    assert not is_running(performed.outcome.value, "nie-ma-takiego-programu-2026")


async def test_asking_for_a_process_that_is_running_finds_it() -> None:
    performed = await REGISTRY.perform("windows.process.list", {"name": "python"})
    assert performed.outcome.evidence["found"] is True
    assert is_running(performed.outcome.value, "python")
