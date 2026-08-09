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
from garis.kernel.contracts import (
    CapabilityTarget,
    EffectDisposition,
    ExecutionContext,
    Failure,
    RuntimeProfile,
)
from garis.kernel.effects import EffectStore
from garis.kernel.outbox import EventOutbox
from garis.kernel.recovery import RecoveryStatus
from garis.runtime import TargetResolver, ToolRegistry
from garis.runtime.action import Action

CAPABILITY = "windows.audio.master.get"
SET = "windows.audio.master.set"
MUTE = "windows.audio.master.mute"


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


# --------------------------------------------------------------- writing back


@pytest.fixture
def endpoint(monkeypatch):
    """A fake endpoint that behaves like one: writes land, reads follow them.

    The point of the whole stage is that GARIS believes the second read rather
    than the first call, so the double has to be able to *disagree* with the
    request — which `quantise` is for.
    """
    state: dict = {"scalar": 0.20, "muted": False, "quantise": None, "fail": None}

    def backend(scalar, muted):
        if state["fail"] is not None:
            raise state["fail"]
        before = audio.AudioReading("{ep-1}", "Głośniki (Realtek)",
                                    state["scalar"], state["muted"])
        if scalar is not None:
            state["scalar"] = (
                state["quantise"](scalar) if state["quantise"] else scalar
            )
        if muted is not None and not state.get("deaf"):
            state["muted"] = bool(muted)
        after = audio.AudioReading("{ep-1}", "Głośniki (Realtek)",
                                   state["scalar"], state["muted"])
        return audio.AudioChange(before=before, after=after)

    monkeypatch.setattr(audio, "_WRITE_BACKEND", backend)
    return state


@pytest.fixture
def writers(capability_runner) -> CapabilityRegistry:
    """All three audio capabilities, admitted on this platform for the envelope."""
    registry = CapabilityRegistry()
    for name in (CAPABILITY, SET, MUTE):
        registry.add(replace(REGISTRY.get(name),
                             platforms=("win32", "linux", "darwin")))
    capability_runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(capability_runner)
    return registry


async def change(name: str, args: dict) -> Outcome:
    executor = REGISTRY.get(name).executor
    return await executor(Invocation(capability=name, args=args))


async def verified(name: str, args: dict):
    outcome = await change(name, args)
    verdict = await REGISTRY.get(name).verifier(
        Invocation(capability=name, args=args), outcome
    )
    return outcome, verdict


async def test_setting_the_volume_is_judged_by_reading_it_back(endpoint) -> None:
    outcome, verdict = await verified(SET, {"percent": 30})

    assert outcome.ok and verdict.ok and verdict.checked
    assert outcome.value["volume_percent"] == 30
    assert outcome.value["requested_percent"] == 30
    assert outcome.value["previous_percent"] == 20, 'stan "przed" pochodzi z pomiaru'
    assert outcome.disposition is EffectDisposition.APPLIED


async def test_an_endpoint_that_rounds_by_a_point_still_counts_as_done(
    endpoint,
) -> None:
    """A driver quantises the master level. 29% and 31% are the hardware being
    honest about 30%, not GARIS missing."""
    endpoint["quantise"] = lambda wanted: 0.31

    outcome, verdict = await verified(SET, {"percent": 30})

    assert outcome.value["volume_percent"] == 31
    assert verdict.ok and verdict.checked


async def test_an_endpoint_that_lands_somewhere_else_is_a_checked_failure(
    endpoint,
) -> None:
    """The write happened and missed. All three facts are held apart: it ran, it
    applied, and the goal was not met."""
    endpoint["quantise"] = lambda wanted: 0.45

    outcome, verdict = await verified(SET, {"percent": 30})

    assert outcome.ok, "wykonanie się udało — to nie jest to samo co cel"
    assert outcome.disposition is EffectDisposition.APPLIED
    assert not verdict.ok and verdict.checked
    assert "45%" in verdict.note and "30%" in verdict.note


async def test_a_write_that_never_reached_the_endpoint_may_be_tried_again(
    endpoint,
) -> None:
    endpoint["fail"] = audio.WriteRefused(
        "Windows nie wskazuje żadnego domyślnego urządzenia odtwarzania.",
        disposition=EffectDisposition.NOT_APPLIED,
    )

    outcome, verdict = await verified(SET, {"percent": 30})

    assert not outcome.ok and not outcome.uncertain
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert outcome.disposition.permits_retry
    assert not verdict.ok and verdict.checked


async def test_a_write_that_may_have_landed_is_never_repeated(endpoint) -> None:
    """A COM call that raised may still have been delivered."""
    endpoint["fail"] = audio.WriteRefused(
        "Nie udało się zmienić ustawień dźwięku: COMError",
        disposition=EffectDisposition.UNKNOWN, uncertain=True,
    )

    outcome, _ = await verified(SET, {"percent": 30})

    assert outcome.uncertain
    assert outcome.disposition is EffectDisposition.UNKNOWN
    assert not outcome.disposition.permits_retry


async def test_a_write_whose_readback_broke_is_applied_and_unverified(
    endpoint,
) -> None:
    """The setter returned; only the check failed. Retrying would set a machine
    that is already set, and claiming success would be a claim nobody made."""
    endpoint["fail"] = audio.WriteRefused(
        "Zmiana poszła do Windows, ale nie udało się sprawdzić wyniku: COMError",
        disposition=EffectDisposition.APPLIED, uncertain=True,
    )

    outcome, verdict = await verified(SET, {"percent": 30})

    assert outcome.disposition is EffectDisposition.APPLIED
    assert outcome.uncertain and not verdict.ok
    assert not outcome.disposition.permits_retry


async def test_running_off_windows_changes_nothing_and_says_so(monkeypatch) -> None:
    monkeypatch.setattr(audio, "_WRITE_BACKEND", audio._unsupported_write)

    outcome, verdict = await verified(SET, {"percent": 30})

    assert not outcome.ok and not outcome.uncertain
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert "Windows" in verdict.note


async def test_a_readback_missing_its_request_cannot_be_verified(endpoint) -> None:
    """Without the request there is nothing to compare against, and a check with
    nothing to compare against is not a check."""
    outcome = await change(SET, {"percent": 30})
    stripped = Outcome(
        ok=True,
        value={k: v for k, v in outcome.value.items() if k != "requested_percent"},
        evidence=outcome.evidence,
    )

    verdict = await audio.verify_master_set(Invocation(SET, {}), stripped)

    assert not verdict.ok and verdict.checked
    assert "requested_percent" in verdict.missing


async def test_evidence_that_disagrees_with_the_request_is_refused(endpoint) -> None:
    outcome = await change(SET, {"percent": 30})
    tampered = Outcome(
        ok=True, value=outcome.value,
        evidence={**outcome.evidence, "requested_percent": 80},
    )

    verdict = await audio.verify_master_set(Invocation(SET, {}), tampered)

    assert not verdict.ok and verdict.checked


# ------------------------------------------------------------------- muting


@pytest.mark.parametrize("wanted", [True, False])
async def test_muting_is_judged_by_reading_the_flag_back(endpoint, wanted) -> None:
    endpoint["muted"] = not wanted

    outcome, verdict = await verified(MUTE, {"muted": wanted})

    assert outcome.ok and verdict.ok and verdict.checked
    assert outcome.value["muted"] is wanted
    assert outcome.value["previous_muted"] is (not wanted)


async def test_a_mute_that_did_not_take_is_a_checked_failure(endpoint) -> None:
    """The endpoint accepted the call and stayed where it was."""
    endpoint["deaf"] = True

    outcome, verdict = await verified(MUTE, {"muted": True})

    assert outcome.ok and outcome.disposition is EffectDisposition.APPLIED
    assert not verdict.ok and verdict.checked


async def test_muting_leaves_the_level_alone(endpoint) -> None:
    """Wyciszenie to nie zerowa głośność."""
    await change(MUTE, {"muted": True})

    assert endpoint["scalar"] == 0.20


# ----------------------------------------------------- the envelope, writing


async def test_the_write_runs_through_the_one_envelope_and_records_its_goal(
    capability_runner, writers, endpoint, db
) -> None:
    result = await capability_runner.run(
        CapabilityTarget(SET),
        Action(tool=SET, params={"percent": 30}, task_id="t1", step_key="set"),
        ExecutionContext(task_id="t1", step_key="set",
                         runtime_profile=RuntimeProfile.TEST),
    )

    assert result.ok and result.verified
    record = EffectStore(db).load(result.effect_id)
    assert record is not None
    assert record.goal == {"volume_percent": 30}, "cel zapisany przed zmianą"
    assert record.disposition is EffectDisposition.APPLIED


async def test_the_same_write_resumed_after_a_crash_keeps_one_identity(
    capability_runner, writers, endpoint
) -> None:
    """An effectful step gets a deterministic id, so a resumed task lands on its
    own reservation instead of setting the volume a second time."""
    async def once():
        return await capability_runner.run(
            CapabilityTarget(SET),
            Action(tool=SET, params={"percent": 30}, task_id="t1", step_key="set"),
            ExecutionContext(task_id="t1", step_key="set",
                             runtime_profile=RuntimeProfile.TEST),
        )

    first, second = await once(), await once()

    assert first.effect_id == second.effect_id
    assert first.effect_id.startswith("eff-")
    assert second.replayed, "sukces jest odtwarzany, nie powtarzany"


async def test_asking_for_an_impossible_volume_never_touches_the_endpoint(
    capability_runner, writers, endpoint
) -> None:
    reached: list[float] = []
    original = audio._WRITE_BACKEND

    def watched(scalar, muted):
        reached.append(scalar)
        return original(scalar, muted)

    audio._WRITE_BACKEND = watched
    try:
        result = await capability_runner.run(
            CapabilityTarget(SET),
            Action(tool=SET, params={"percent": 250}, task_id="t1", step_key="set"),
            ExecutionContext(task_id="t1", step_key="set",
                             runtime_profile=RuntimeProfile.TEST),
        )
    finally:
        audio._WRITE_BACKEND = original

    assert not result.ok and result.failure is Failure.INVALID_INPUT
    assert reached == [], "walidacja jest przed rezerwacją i przed zapisem"
    assert result.disposition is EffectDisposition.NOT_STARTED


# ------------------------------------------------------------------ recovery


def crashed(db, capability_id: str, goal: dict, effect_id: str = "eff-audio"):
    """The footprint of a process that died mid-write."""
    effects = EffectStore(db)
    effects.reserve(effect_id, capability_id=capability_id, task_id="t1",
                    step_key="set", args={}, goal=goal)
    effects.sweep_unsettled()
    return effects.load(effect_id)


@pytest.fixture
def recovery(capability_runner, writers, db, bus):
    from garis.kernel.recovery import RecoveryStore
    from garis.runtime import AuditLog
    from garis.runtime.recovery import EffectRecoveryService

    return EffectRecoveryService(
        effects=EffectStore(db),
        recoveries=RecoveryStore(db),
        runner=capability_runner,
        resolver=TargetResolver(ToolRegistry(), writers),
        capabilities=writers,
        audit=AuditLog(db),
        outbox=EventOutbox(db, bus),
    )


async def test_after_a_crash_the_volume_is_measured_and_never_set_again(
    recovery, db, endpoint, speakers
) -> None:
    """The whole reason `audio.set` was the case recovery was built for."""
    crashed(db, SET, {"volume_percent": 30})
    speakers(reading(0.30))

    wrote: list[float] = []
    original = audio._WRITE_BACKEND
    audio._WRITE_BACKEND = lambda s, m: wrote.append(s)  # type: ignore[assignment]
    try:
        outcome = await recovery.reconcile("eff-audio")
    finally:
        audio._WRITE_BACKEND = original

    assert wrote == [], "recovery ogląda świat, nigdy nie powtarza operacji"
    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert outcome.verification.goal_met and outcome.verification.checked


async def test_a_matching_volume_is_never_proof_that_garis_set_it(
    recovery, db, speakers
) -> None:
    """Someone can reach for the volume key while the engine is down."""
    crashed(db, SET, {"volume_percent": 30})
    speakers(reading(0.30))

    outcome = await recovery.reconcile("eff-audio")

    assert outcome.disposition is EffectDisposition.UNKNOWN
    assert not outcome.disposition.permits_retry


async def test_a_volume_that_is_not_what_was_wanted_is_reported_as_such(
    recovery, db, speakers
) -> None:
    crashed(db, SET, {"volume_percent": 30})
    speakers(reading(0.65))

    outcome = await recovery.reconcile("eff-audio")

    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert not outcome.verification.goal_met
    assert outcome.verification.checked
    assert "65%" in outcome.verification.reason


async def test_recovery_says_so_when_nobody_wrote_down_the_goal(
    recovery, db, speakers
) -> None:
    """A row from before goals existed. Comparing the world against a guess would
    be worse than admitting there is nothing to compare against."""
    effects = EffectStore(db)
    effects.reserve("eff-audio", capability_id=SET, task_id="t1", step_key="set",
                    args={})
    effects.sweep_unsettled()
    speakers(reading(0.30))

    outcome = await recovery.reconcile("eff-audio")

    assert outcome.status is RecoveryStatus.STILL_UNKNOWN
    assert not outcome.verification.goal_met


async def test_a_crashed_mute_is_settled_by_the_flag_it_wanted(
    recovery, db, speakers
) -> None:
    crashed(db, MUTE, {"muted": True})
    speakers(reading(0.30, muted=True))

    outcome = await recovery.reconcile("eff-audio")

    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert outcome.verification.goal_met and outcome.verification.checked


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


async def test_a_capability_that_does_not_run_here_says_so_rather_than_blaming_the_caller(
    capability_runner,
) -> None:
    """The wrong host is an unsupported platform, not a bad parameter — and a
    caller deciding whether to retry reads that field.

    The platform list is rewritten rather than relied on, so the test means the
    same thing on Windows as it does here.
    """
    registry = CapabilityRegistry()
    registry.add(replace(REGISTRY.get(SET), platforms=("plan9",)))
    capability_runner.register_executor("capability", NativeCapabilityExecutor(registry))

    result = await capability_runner.run(
        CapabilityTarget(SET),
        Action(tool=SET, params={"percent": 30}),
        ExecutionContext(runtime_profile=RuntimeProfile.TEST),
    )

    assert result.failure is Failure.UNSUPPORTED_PLATFORM
    assert result.disposition is EffectDisposition.NOT_STARTED
    assert not result.retryable


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


def test_a_write_is_only_ever_judged_by_the_same_measurement_as_a_read() -> None:
    """The rule that replaced "this stage cannot write".

    E1 was read-only and a test held it there. E2 writes, so the guarantee moves
    rather than disappearing: every setter is followed by `_observe`, the one
    measurement the reader also uses, and no verifier is allowed to conclude
    anything from the setter having returned.
    """
    import inspect

    source = inspect.getsource(audio._write_windows)

    assert "SetMasterVolumeLevelScalar" in source and "SetMute" in source
    assert source.index("_observe") < source.index("SetMasterVolumeLevelScalar"), (
        "stan sprzed zmiany musi być zmierzony, zanim cokolwiek się zmieni"
    )
    assert source.rindex("_observe") > source.rindex("SetMute"), (
        "po zapisie musi nastąpić odczyt — inaczej nie ma czego weryfikować"
    )


@pytest.mark.parametrize(
    "verifier, echo",
    [
        ("verify_master_set", {"requested_percent": 30}),
        ("verify_master_mute", {"requested_muted": True}),
    ],
)
async def test_a_result_that_only_echoes_the_request_is_refused(verifier, echo) -> None:
    """The original defect in this capability's shape: a "success" whose entire
    content is the thing that was asked for."""
    verdict = await getattr(audio, verifier)(
        Invocation(SET, {}), Outcome(ok=True, value=dict(echo), evidence=dict(echo))
    )

    assert not verdict.ok and verdict.checked


def test_the_conversion_has_exactly_one_definition() -> None:
    """The verifier and the capability must not drift apart on a boundary."""
    import inspect

    source = inspect.getsource(audio)

    assert source.count("* 100") == 1, "druga konwersja to druga prawda"
    assert math.isclose(audio.percent_of(0.5), 50)
