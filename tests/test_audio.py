"""Głośność zmierzona, nie założona.

E1 — the first capability that reads real Windows state. It matters more than
its size: `audio.set` will be judged by reading back through this one, and
recovery after a crash will inspect with it. A reading that can be wrong without
saying so would poison both.

**Nothing here has run on Windows.** The suite runs on Linux, so the Core Audio
call itself is exercised through the module's one seam (`_BACKEND`). Everything
above that seam — the conversion, the verifier, the envelope, the catalogue — is
the real production code.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from garis.capabilities import audio
from garis.capabilities.base import Invocation, Outcome
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.registry import REGISTRY, CapabilityRegistry
from garis.errors import ExecutionError, Unsupported
from garis.kernel.contracts import CapabilityTarget, ExecutionContext, RuntimeProfile
from garis.kernel.effects import EffectStore
from garis.runtime import TargetResolver, ToolRegistry
from garis.runtime.action import Action

CAPABILITY = "windows.audio.master.get"


def reading(scalar: float = 0.42, muted: bool = False, endpoint: str = "{ep-1}"):
    return audio.AudioReading(
        endpoint_id=endpoint, endpoint_name="Głośniki (Realtek)",
        volume_scalar=scalar, muted=muted,
    )


@pytest.fixture
def speakers(monkeypatch):
    """The seam. A fake only where the operating system would be."""

    def install(value):
        def backend():
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(audio, "_BACKEND", backend)

    return install


async def run(invocation_args=None) -> Outcome:
    return await audio._get_master(
        Invocation(capability=CAPABILITY, args=invocation_args or {})
    )


# ------------------------------------------------------------------- contract


def test_the_capability_declares_everything_registration_now_requires() -> None:
    capability = REGISTRY.get(CAPABILITY)

    assert capability.id == CAPABILITY
    assert capability.version == 1
    assert capability.summary
    assert capability.platforms == ("win32",)
    assert capability.exposed
    assert capability.evidence, "bez schematu dowodów nie ma czego sprawdzać"
    assert capability.outputs


def test_reading_the_volume_declares_no_effects_explicitly() -> None:
    """`SYSTEM_READ` would equally cover a capability that *changes* the volume,
    so the empty set has to be stated rather than implied by the permission."""
    capability = REGISTRY.get(CAPABILITY)

    assert capability.effects == frozenset()
    assert capability.reversible
    assert not capability.fixture


def test_the_capability_is_marked_windows_only() -> None:
    assert REGISTRY.get(CAPABILITY).platforms == ("win32",)


# ----------------------------------------------------------------- conversion


@pytest.mark.parametrize(
    "scalar,percent",
    [(0.0, 0), (1.0, 100), (0.42, 42), (0.425, 43), (0.005, 1), (0.994, 99),
     (0.375, 38), (0.435, 44)],
)
def test_the_percentage_follows_from_the_scalar(scalar, percent) -> None:
    assert audio.percent_of(scalar) == percent


def test_the_percentage_is_derived_and_never_measured_separately() -> None:
    """One number comes from Windows; the other is arithmetic on it.

    Two readings could disagree, and then the check would be about their
    agreement rather than about the truth of either.
    """
    observed = reading(scalar=0.37)

    assert observed.volume_percent == audio.percent_of(0.37)
    assert observed.to_value()["volume_percent"] == observed.volume_percent


# --------------------------------------------------------------- verification


async def verdict(outcome: Outcome):
    return await audio.verify_master_reading(
        Invocation(capability=CAPABILITY), outcome
    )


async def test_a_consistent_reading_is_checked_and_accepted(speakers) -> None:
    speakers(reading(0.42, muted=False))
    outcome = await run()

    assert outcome.ok
    result = await verdict(outcome)
    assert result.ok and result.checked


async def test_a_muted_endpoint_reads_as_muted(speakers) -> None:
    speakers(reading(0.42, muted=True))
    outcome = await run()

    assert outcome.value["muted"] is True
    assert (await verdict(outcome)).ok


async def test_an_unmuted_endpoint_reads_as_unmuted(speakers) -> None:
    speakers(reading(0.42, muted=False))
    outcome = await run()

    assert outcome.value["muted"] is False
    assert (await verdict(outcome)).ok


async def test_a_percentage_that_does_not_follow_is_refused() -> None:
    """The check that makes the derivation load-bearing rather than decorative."""
    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "{ep}", "volume_scalar": 0.42, "volume_percent": 80,
               "muted": False},
        evidence={"endpoint_id": "{ep}", "volume_scalar": 0.42, "volume_percent": 80,
                  "muted": False},
    )

    result = await verdict(outcome)

    assert not result.ok and result.checked
    assert "nie wynika" in result.note


@pytest.mark.parametrize("scalar", [1.5, -0.1, 42])
async def test_a_scalar_outside_zero_to_one_is_refused(scalar) -> None:
    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "{ep}", "volume_scalar": scalar,
               "volume_percent": audio.percent_of(scalar), "muted": False},
        evidence={"endpoint_id": "{ep}", "volume_scalar": scalar,
                  "volume_percent": audio.percent_of(scalar), "muted": False},
    )

    result = await verdict(outcome)

    assert not result.ok and result.checked


async def test_not_a_number_is_refused_for_not_being_finite() -> None:
    """NaN passes every range comparison in silence, so it is caught on
    `isfinite` rather than on the bounds check below it."""
    nan = float("nan")
    assert not (0.0 <= nan <= 1.0) and not (nan > 1.0), "NaN przechodzi porównania cicho"

    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "{ep}", "volume_scalar": nan, "volume_percent": 0,
               "muted": False},
        evidence={"endpoint_id": "{ep}", "volume_scalar": nan, "volume_percent": 0,
                  "muted": False},
    )

    result = await verdict(outcome)

    assert not result.ok and result.checked
    assert "skończoną" in result.note


async def test_a_reading_with_no_endpoint_identity_is_refused() -> None:
    """`audio.set` has to be able to prove it changed what this measured."""
    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "", "volume_scalar": 0.4, "volume_percent": 40,
               "muted": False},
        evidence={"endpoint_id": "", "volume_scalar": 0.4, "volume_percent": 40,
                  "muted": False},
    )

    result = await verdict(outcome)

    assert not result.ok and "urządzenia" in result.note


async def test_evidence_that_contradicts_the_result_is_refused() -> None:
    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "{ep}", "volume_scalar": 0.4, "volume_percent": 40,
               "muted": False},
        evidence={"endpoint_id": "{ep}", "volume_scalar": 0.4, "volume_percent": 40,
                  "muted": True},
    )

    result = await verdict(outcome)

    assert not result.ok and "Dowód" in result.note


async def test_a_mute_flag_that_is_not_a_boolean_is_refused() -> None:
    outcome = Outcome(
        ok=True,
        value={"endpoint_id": "{ep}", "volume_scalar": 0.4, "volume_percent": 40,
               "muted": 1},
        evidence={"endpoint_id": "{ep}", "volume_scalar": 0.4, "volume_percent": 40,
                  "muted": 1},
    )

    assert not (await verdict(outcome)).ok


# ------------------------------------------------------------------- failures


async def test_a_dead_audio_stack_is_a_checked_failure_not_zero_percent(speakers) -> None:
    """The distinction this capability exists to keep.

    "I could not look" and "the volume is zero" are different sentences, and
    only one of them is true here.
    """
    speakers(ExecutionError("Nie udało się odczytać głośności z Windows: COMError"))
    outcome = await run()

    assert not outcome.ok
    assert outcome.value is None, "porażka nie udaje odczytu"
    result = await verdict(outcome)
    assert not result.ok and result.checked, "porażka jest sprawdzona, nie zmyślona"


async def test_no_default_endpoint_is_not_reported_as_silence(speakers) -> None:
    speakers(ExecutionError(
        "Windows nie wskazuje żadnego domyślnego urządzenia odtwarzania."
    ))
    outcome = await run()

    assert not outcome.ok
    assert "0%" not in outcome.error and "wycisz" not in outcome.error.lower()
    assert "urządzenia odtwarzania" in outcome.error


async def test_running_off_windows_says_so_rather_than_guessing(speakers) -> None:
    speakers(Unsupported("Odczyt głośności działa tylko na Windows."))
    outcome = await run()

    assert not outcome.ok
    assert "Windows" in outcome.error
    assert outcome.evidence["platform"]


# -------------------------------------------------------------- the envelope


@pytest.fixture
def catalogue(capability_runner) -> CapabilityRegistry:
    """The real capability, registered so it runs on this host.

    `platforms=("win32",)` is correct and is exactly what stops it running here,
    so the envelope tests register a copy that admits this platform. Everything
    else about it — executor, verifier, evidence schema — is untouched.
    """
    registry = CapabilityRegistry()
    registry.add(replace(REGISTRY.get(CAPABILITY), platforms=("win32", "linux", "darwin")))
    capability_runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(capability_runner)
    # The shared catalogue is deliberately left alone: this capability carries
    # its own verifier, so nothing resolves it by id the way `evidence_verifier`
    # would have.
    return registry


async def test_the_reading_runs_through_the_one_envelope(
    capability_runner, catalogue, speakers, db
) -> None:
    """The production path, not the adapter."""
    speakers(reading(0.42))

    entered: list[str] = []
    original = capability_runner.run

    def watched(target, action, context=None):
        entered.append(target.name)
        return original(target, action, context)

    capability_runner.run = watched  # type: ignore[method-assign]
    result = await capability_runner.run(
        CapabilityTarget(CAPABILITY),
        Action(tool=CAPABILITY, params={}, task_id="t1", step_key="vol"),
        ExecutionContext(task_id="t1", step_key="vol", runtime_profile=RuntimeProfile.TEST),
    )

    assert entered == [CAPABILITY]
    assert result.ok
    assert result.verification.checked and result.verification.goal_met
    assert result.value["volume_percent"] == 42


async def test_the_measurement_is_persisted_as_evidence(
    capability_runner, catalogue, speakers, db
) -> None:
    speakers(reading(0.42))

    result = await capability_runner.run(
        CapabilityTarget(CAPABILITY),
        Action(tool=CAPABILITY, params={}, task_id="t1", step_key="vol"),
        ExecutionContext(task_id="t1", step_key="vol", runtime_profile=RuntimeProfile.TEST),
    )

    stored = EffectStore(db).load(result.effect_id)
    assert stored is not None
    assert stored.verification["checked"] is True
    assert any("volume_scalar" in str(item) for item in stored.evidence)


async def test_a_reading_gets_a_fresh_effect_id_every_time(
    capability_runner, catalogue, speakers
) -> None:
    """Replaying a measurement would hand back a stale number dressed as a
    current one — the reason reads are never given a deterministic id."""
    speakers(reading(0.42))

    async def once():
        return await capability_runner.run(
            CapabilityTarget(CAPABILITY),
            Action(tool=CAPABILITY, params={}, task_id="t1", step_key="vol"),
            ExecutionContext(task_id="t1", step_key="vol",
                             runtime_profile=RuntimeProfile.TEST),
        )

    first, second = await once(), await once()

    assert first.effect_id != second.effect_id
    assert first.effect_id.startswith("inv-")


# ------------------------------------------------------- catalogue and reflex


def test_the_planner_can_see_it_on_windows() -> None:
    """Exposed, and offered wherever it runs.

    On this Linux host the catalogue correctly withholds it — `supported_here()`
    is false — so the menu is asked about a copy that admits this platform. The
    filtering is the existing behaviour and is not being worked around.
    """
    assert REGISTRY.get(CAPABILITY).exposed

    elsewhere = CapabilityRegistry()
    elsewhere.add(replace(REGISTRY.get(CAPABILITY),
                          platforms=("win32", "linux", "darwin")))
    menu = TargetResolver(ToolRegistry(), elsewhere).menu()

    assert CAPABILITY in {c["id"] for c in menu["capabilities"]}


def test_the_catalogue_withholds_it_where_it_cannot_run() -> None:
    """Not a gap: offering a Windows-only reading on Linux would have the
    planner schedule a step the resolver then refuses."""
    import sys

    offered = {c["id"] for c in REGISTRY.catalogue()}

    assert (CAPABILITY in offered) == (sys.platform == "win32")


def test_it_is_a_real_capability_and_not_a_fixture() -> None:
    """Production must contain this one, and must refuse the stand-ins."""
    from garis import profiles

    contents = profiles.inspect(RuntimeProfile.PRODUCTION, capabilities=REGISTRY.all())

    assert CAPABILITY in contents.capabilities
    assert CAPABILITY not in contents.fixtures


@pytest.mark.parametrize(
    "phrase", ["jaka jest głośność", "ile mam głośności", "czy komputer jest wyciszony"]
)
def test_the_questions_people_ask_reach_the_reflex(phrase) -> None:
    from garis.agent import reflex

    found = reflex.find(phrase)

    assert found is not None and found.name == "audio-volume"
    assert found.target == CapabilityTarget(CAPABILITY)


def test_asking_to_change_the_volume_is_not_a_reflex() -> None:
    """A reflex reads. Changing the volume needs a plan, an effect and a gate."""
    from garis.agent import reflex

    assert reflex.find("podgłośnij do 30%") is None
    assert reflex.find("ustaw głośność na 30") is None


def test_the_reflex_plan_names_a_capability_and_nothing_else() -> None:
    from garis.agent import reflex

    plan = reflex.plan_for("jaka jest głośność", available=lambda _: True)

    assert plan is not None
    assert [s.target for s in plan.steps] == [CapabilityTarget(CAPABILITY)]
    assert plan.origin == reflex.ORIGIN


def test_the_reflex_rechecks_the_arithmetic_rather_than_trusting_it() -> None:
    from garis.agent import reflex

    target = CapabilityTarget(CAPABILITY)
    good = reading(0.42).to_value()

    assert reflex.check(target, good) == ""
    assert reflex.answer(target, good) == "Głośność: 42%."

    lying = {**good, "volume_percent": 80}
    assert reflex.check(target, lying)
    assert reflex.answer(target, lying) == ""


def test_the_sentence_is_built_from_the_verified_structure() -> None:
    assert audio.describe(reading(0.42, muted=False).to_value()) == "Głośność: 42%."
    assert "wyciszony" in audio.describe(reading(0.42, muted=True).to_value())
    assert audio.describe("42%") == "", "proza nie jest źródłem"


# ----------------------------------------------------------------- structural


def test_nothing_reaches_the_windows_adapter_except_the_capability() -> None:
    """Reflex, planner, CLI and reporter all go through the envelope."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "core" / "src" / "garis"
    pattern = re.compile(r"pycaw|comtypes|_read_windows|_BACKEND")

    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if pattern.search(line) and path.name != "audio.py"
    ]

    assert offenders == [], f"ktoś woła Windows z pominięciem koperty: {offenders}"


def test_this_stage_contains_no_way_to_change_the_volume() -> None:
    """E1 is read-only. Nothing here can set, mute or unmute anything."""
    import inspect

    source = inspect.getsource(audio)

    for forbidden in ("SetMasterVolumeLevelScalar", "SetMute", "SetMasterVolumeLevel",
                      "audio.master.set", "audio.mute"):
        assert forbidden not in source, f"E1 dorobiło zapis: {forbidden}"


def test_the_conversion_has_exactly_one_definition() -> None:
    """The verifier and the capability must not drift apart on a boundary."""
    import inspect

    source = inspect.getsource(audio)

    assert source.count("* 100") == 1, "druga konwersja to druga prawda"
    assert math.isclose(audio.percent_of(0.5), 50)
