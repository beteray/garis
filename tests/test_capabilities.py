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
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.processes import is_running
from garis.errors import Unsupported
from garis.kernel import Effect, ToolTarget


def bind(registry: CapabilityRegistry, runner) -> CapabilityRegistry:
    """Attach a catalogue to the one envelope, the way ``app.build`` does.

    Defined here rather than imported from ``conftest``: pytest loads that file
    as top-level ``conftest``, so ``from tests.conftest import ...`` hands back a
    second copy of the module.
    """
    runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(runner)
    return registry


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


async def test_a_capability_runs_and_reports_what_it_proved(capability_runner, perform) -> None:
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(_capability())
    # `evidence_verifier` looks the capability up in the shared registry, so the
    # local one has to be findable there too.
    REGISTRY.add(_capability())

    performed = await perform(registry, "test.thing")

    assert performed.ok and performed.verified
    assert performed.verdict.checked
    assert performed.to_dict()["evidence"] == {"seen": 1}


async def test_missing_evidence_is_not_a_verified_result(capability_runner, perform) -> None:
    async def run(_: Invocation) -> Outcome:
        return Outcome(ok=True, value={"done": True}, evidence={})   # nothing measured

    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(_capability(id="test.silent", executor=run))
    REGISTRY.add(_capability(id="test.silent", executor=run))

    performed = await perform(registry, "test.silent")

    assert performed.ok, "wykonanie się powiodło"
    assert not performed.verified, "ale nic tego nie potwierdziło"
    assert performed.verdict.missing == ("seen",)


async def test_a_capability_with_no_postcondition_says_so(capability_runner, perform) -> None:
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(_capability(id="test.unchecked", verifier=always_unchecked, evidence=()))

    performed = await perform(registry, "test.unchecked")

    assert performed.ok
    assert not performed.verified
    assert not performed.verdict.checked


async def test_evidence_that_fails_its_own_check_is_refused(capability_runner, perform) -> None:
    async def run(_: Invocation) -> Outcome:
        return Outcome(ok=True, evidence={"scanned": 0})

    spec = _capability(
        id="test.zero",
        executor=run,
        evidence=(Field("scanned", "int", "Ile odczytano",
                        check=lambda v: isinstance(v, int) and v > 0),),
    )
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)
    REGISTRY.add(spec)

    performed = await perform(registry, "test.zero")
    assert not performed.verified
    assert performed.verdict.missing == ("scanned",)


async def test_an_executor_that_crashes_produces_neither_success_nor_silence(
    capability_runner,
    perform,
) -> None:
    async def boom(_: Invocation) -> Outcome:
        raise RuntimeError("bum")

    spec = _capability(id="test.boom", executor=boom, risk=Risk.REVERSIBLE,
                       effects=frozenset({Effect.WRITE}))
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)
    REGISTRY.add(spec)

    performed = await perform(registry, "test.boom")

    assert not performed.ok and not performed.verified
    assert "bum" in performed.outcome.error
    # It changed something or it did not; nobody knows, and that is recorded.
    assert performed.outcome.uncertain


async def test_an_unsupported_platform_is_answered_before_anything_runs(
    capability_runner,
    perform,
) -> None:
    ran = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        ran["count"] += 1
        return Outcome(ok=True)

    other = "linux" if sys.platform == "win32" else "win32"
    spec = _capability(id="test.elsewhere", executor=run, platforms=(other,))
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)

    performed = await perform(registry, "test.elsewhere")

    assert not performed.ok and not performed.verified
    assert ran["count"] == 0, "nie wolno uruchamiać czegoś, co tu nie działa"
    assert "nie działa na tym systemie" in performed.outcome.error


async def test_bad_arguments_stop_before_the_executor(capability_runner, perform) -> None:
    ran = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        ran["count"] += 1
        return Outcome(ok=True, evidence={"seen": 1})

    spec = _capability(
        id="test.args",
        executor=run,
        inputs=(Field("level", "int", "Poziom", required=True),),
    )
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)

    performed = await perform(registry, "test.args", {})
    assert not performed.ok and ran["count"] == 0
    assert "brakuje parametru" in performed.outcome.error


async def test_unknown_capabilities_are_not_invented(capability_runner, perform) -> None:
    with pytest.raises(Unsupported):
        await perform(bind(CapabilityRegistry(), capability_runner), "windows.nothing.here")


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


async def test_the_same_effect_is_not_performed_twice(capability_runner, perform) -> None:
    """A resume after a crash must not repeat a real change."""
    runs = {"count": 0}

    async def run(_: Invocation) -> Outcome:
        runs["count"] += 1
        return Outcome(ok=True, value={"run": runs["count"]}, evidence={"seen": 1})

    spec = _capability(id="test.once", executor=run, risk=Risk.IRREVERSIBLE,
                       effects=frozenset({Effect.WRITE}))
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)
    REGISTRY.add(spec)

    first = await perform(registry, "test.once", effect_id="launch-discord")
    second = await perform(registry, "test.once", effect_id="launch-discord")

    assert runs["count"] == 1, "drugi przebieg wykonałby efekt jeszcze raz"
    assert second.outcome.value == first.outcome.value


async def test_a_failed_effect_may_be_attempted_again(capability_runner, perform) -> None:
    """Refusing to retry a failure would strand the task; only success is final."""
    runs = {"count": 0}

    async def flaky(_: Invocation) -> Outcome:
        runs["count"] += 1
        ok = runs["count"] > 1
        return Outcome(ok=ok, evidence={"seen": 1} if ok else {},
                       error="" if ok else "nie tym razem")

    spec = _capability(id="test.flaky", executor=flaky)
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(spec)
    REGISTRY.add(spec)

    assert not (await perform(registry, "test.flaky", effect_id="e1")).ok
    assert (await perform(registry, "test.flaky", effect_id="e1")).ok
    assert runs["count"] == 2


# ---------------------------------------------------------------- processes


async def test_the_process_list_measures_this_machine(capability_runner, perform) -> None:
    catalogue = bind(REGISTRY, capability_runner)
    performed = await perform(catalogue, "windows.process.list", {"limit": 500})

    assert performed.ok and performed.verified
    assert performed.outcome.evidence["scanned"] > 0
    names = [row["name"] for row in performed.outcome.value["processes"]]
    assert names, "żaden proces nie został odczytany"
    # This test is itself a running Python process; if the reading were invented
    # it would have no reason to contain one.
    assert any("python" in name.lower() for name in names), names[:20]


async def test_a_named_process_is_only_claimed_when_it_was_seen(capability_runner, perform) -> None:
    performed = await perform(
        bind(REGISTRY, capability_runner),
        "windows.process.list", {"name": "nie-ma-takiego-programu-2026"},
    )

    assert performed.ok, "odczyt się udał"
    assert performed.outcome.evidence["found"] is False
    assert performed.outcome.value["matched"] == 0
    assert not is_running(performed.outcome.value, "nie-ma-takiego-programu-2026")


async def test_asking_for_a_process_that_is_running_finds_it(capability_runner, perform) -> None:
    catalogue = bind(REGISTRY, capability_runner)
    performed = await perform(catalogue, "windows.process.list", {"name": "python"})
    assert performed.outcome.evidence["found"] is True
    assert is_running(performed.outcome.value, "python")


# ------------------------------------------------------- asking about a program


async def test_the_process_reflexes_reach_a_real_reading(runtime, capability_runner) -> None:
    """The two phrases a person actually types, answered without a model."""
    from garis.agent import reflex
    from garis.tools import system

    system.register(runtime.registry)

    listing = reflex.plan_for("pokaż uruchomione procesy")
    assert listing is not None and listing.steps[0].target == ToolTarget("process_find")
    assert listing.steps[0].params == {}

    named = reflex.plan_for("czy Discord jest uruchomiony")
    assert named is not None and named.steps[0].params == {"name": "discord"}


def test_a_question_that_names_no_program_gets_no_reflex() -> None:
    """Guessing which program someone meant is worse than handing it on."""
    from garis.agent import reflex

    assert reflex.plan_for("czy działa") is None
    assert reflex.plan_for("czy to jest uruchomione") is None


def test_a_failed_process_reading_is_not_an_empty_desktop() -> None:
    """Zero processes means the reading failed. No machine has none."""
    from garis.agent import reflex

    assert reflex.check(ToolTarget("process_find"), {"scanned": 0, "matched": []})
    assert reflex.answer(ToolTarget("process_find"), {"scanned": 0, "matched": []}) == ""


def test_a_program_is_only_reported_as_running_when_it_was_seen() -> None:
    from garis.agent import reflex

    absent = {"query": "discord", "scanned": 214, "matched": [], "match_count": 0,
              "found": False}
    assert reflex.check(ToolTarget("process_find"), absent) == ""
    sentence = reflex.answer(ToolTarget("process_find"), absent)
    assert "Nie widzę" in sentence and "214" in sentence

    present = {"query": "discord", "scanned": 214, "match_count": 2,
               "matched": [{"pid": 4120, "name": "Discord.exe", "memory_mb": 210.0},
                           {"pid": 4188, "name": "Discord.exe", "memory_mb": 88.0}],
               "found": True}
    answer = reflex.answer(ToolTarget("process_find"), present)
    assert "działa" in answer and "4120" in answer and "2 procesy" in answer


# ------------------------------------------- Etap C: the catalogue does not run


def test_the_catalogue_has_no_way_to_run_anything() -> None:
    """A list, not an engine.

    `CapabilityRegistry.perform` used to build an action and call the runner. It
    decided nothing, but it was a second door into execution that no production
    code walked through — it survived because tests were shorter with it. Tests
    now go through the runner, which is where the guarantees live.
    """
    forbidden = {"perform", "run", "execute", "invoke", "call", "forget_effects"}

    assert forbidden.isdisjoint(dir(CapabilityRegistry)), (
        f"katalog znowu potrafi wykonywać: {forbidden & set(dir(CapabilityRegistry))}"
    )


def test_nothing_in_the_engine_asks_the_catalogue_to_run_something() -> None:
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "core" / "src" / "garis"
    pattern = re.compile(r"(REGISTRY|catalogue|capabilities|registry)\.perform\b")

    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line)
    ]

    assert offenders == [], f"ktoś wykonuje przez katalog: {offenders}"


def test_a_capability_that_changes_the_world_must_say_what_it_changes() -> None:
    """No bridge, no fallback, no guess.

    There used to be one: a mutating capability with no declared effects was
    quietly given `Effect.WRITE` and a DeprecationWarning. Convenient, and wrong
    in both directions — under-declaring is a missing gate, over-declaring is a
    confirmation dialogue for a volume change. Only the author can decide.
    """
    registry = CapabilityRegistry()

    with pytest.raises(ValueError, match="effects="):
        registry.add(_capability(id="test.mutates", risk=Risk.IRREVERSIBLE,
                                 effects=frozenset()))


def test_a_reading_declares_an_empty_set_and_is_accepted() -> None:
    """"Touches nothing" is a legitimate answer — it just has to be given."""
    registry = CapabilityRegistry()
    registry.add(_capability(id="test.reads", risk=Risk.READ, effects=frozenset()))

    assert registry.get("test.reads").effects == frozenset()


def test_permission_is_never_read_as_a_statement_of_effect() -> None:
    """`SYSTEM_READ` covers listing processes; it would also cover killing one."""
    registry = CapabilityRegistry()

    with pytest.raises(ValueError):
        registry.add(_capability(id="test.permissioned", risk=Risk.REVERSIBLE,
                                 permission=Permission.SYSTEM_READ, effects=frozenset()))


def test_the_legacy_effect_bridge_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        import garis.capabilities.legacy_effects  # noqa: F401


def test_registering_the_real_capabilities_warns_about_nothing() -> None:
    """The suite must not carry a deprecation the engine no longer has."""
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        registry = CapabilityRegistry()
        for capability in REGISTRY.all():
            registry.add(capability)
