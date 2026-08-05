"""Retrying is a claim about the world, not a reaction to an exception.

"It failed, so try again" is correct for a disk reading and dangerous for
anything else. An executor that sent the message and then died on the readback
failed *and* applied; one that could not open the socket failed and applied
nothing. They raise identically, and the difference between them is the
difference between sending a message once and sending it twice.

So GARIS keeps five questions apart, and never lets one answer another:

    Did the code run?          Outcome.ok
    Did the world change?      EffectDisposition
    Was the goal met?          Verification.goal_met
    Did anyone check?          Verification.checked
    Do we know the result?     not Verification.uncertain

These tests hold that separation at every seam it could collapse at.
"""

from __future__ import annotations

import pytest

from garis.agent.goal import Goal, Plan
from garis.agent.report import build_report
from garis.agent.verify import StepEvidence
from garis.capabilities.base import (
    Capability,
    Field,
    Invocation,
    Outcome,
    Permission,
    Risk,
)
from garis.capabilities.base import (
    Verdict as CapVerdict,
)
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.registry import CapabilityRegistry
from garis.errors import ApprovalDenied, ApprovalRequired, PolicyDenied
from garis.kernel.contracts import (
    Effect,
    EffectDisposition,
    ExecutionContext,
    Failure,
    RuntimeProfile,
    ToolTarget,
    Verification,
)
from garis.kernel.effects import EffectState
from garis.runtime.action import Action
from garis.runtime.leases import LeaseMode
from garis.runtime.registry import ParamSpec
from garis.runtime.runner import ExecutionOutput, decide_disposition

# ------------------------------------------------------------------- helpers


async def _saw_delivery(_: Invocation, outcome: Outcome) -> CapVerdict:
    """A postcondition local to this file.

    `evidence_verifier` resolves the capability through the *shared* registry,
    which would mean every test here had to register into it and then live with
    whatever another test left behind.
    """
    if not outcome.ok:
        return CapVerdict(ok=False, checked=True, note=outcome.error or "Nie powiodło się.")
    if "delivered" not in outcome.evidence:
        return CapVerdict(ok=False, checked=True, note="Brak dowodu dostarczenia.",
                          missing=("delivered",))
    return CapVerdict(ok=True, checked=True, note="Sprawdziłem wynik po fakcie.")


def bind(registry: CapabilityRegistry, runner) -> CapabilityRegistry:
    runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(runner)
    return registry


def sender(runs: list[str], **over) -> Capability:
    """An effectful capability: it changes something outside GARIS."""

    async def run(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        return Outcome(ok=True, value={"sent": True}, evidence={"delivered": 1})

    base = dict(
        id="safety.send", version=1, summary="Wysyła.", risk=Risk.REVERSIBLE,
        permission=Permission.NETWORK, executor=run, verifier=_saw_delivery,
        effects=frozenset({Effect.WRITE}),
        evidence=(Field("delivered", "int", "Ile dostarczono"),),
    )
    return Capability(**{**base, **over})


def effects_rows(db) -> list[dict]:
    return [dict(r) for r in db.query("SELECT * FROM effects ORDER BY rowid")]


def audit_rows(db, tool: str) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM audit WHERE tool = ? ORDER BY id", (tool,)
    )]


# ------------------------------------------- nothing happens before the gates


async def test_a_policy_refusal_leaves_no_effect_at_all(runtime, paths, db) -> None:
    with pytest.raises(PolicyDenied):
        await runtime.perform_tool("note", text=str(paths.key_file))

    assert effects_rows(db) == []
    assert audit_rows(db, "note")[0]["detail"].startswith("[not_started]")


async def test_a_refused_approval_leaves_no_effect_at_all(runtime, db) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("publish", text="wpis")
    runtime.approvals.resolve(caught.value.request_id, False)

    with pytest.raises(ApprovalDenied):
        await runtime.perform_tool("publish", text="wpis")

    assert effects_rows(db) == []
    assert all(r["detail"].startswith("[not_started]") for r in audit_rows(db, "publish"))


async def test_a_busy_resource_leaves_no_effect_at_all(runtime, db) -> None:
    """Waiting for a lock is not doing the work, so nothing may be reserved."""

    @runtime.registry.tool(
        "guarded", "Pisze do zajętego pliku.",
        params={"text": ParamSpec("string", required=True)},
        effects=[Effect.WRITE], resources=lambda p: ["file:contended"], timeout=0.05,
    )
    async def guarded(ctx, text):  # type: ignore[no-untyped-def]
        return {"written": text}

    held = await runtime.leases.acquire(
        ["file:contended"], holder="ktos-inny", mode=LeaseMode.EXCLUSIVE
    )
    try:
        result = await runtime.perform_tool("guarded", text="x")
    finally:
        held.release()

    assert not result.ok and result.error_kind == "busy"
    assert effects_rows(db) == []
    assert audit_rows(db, "guarded")[0]["detail"].startswith("[not_started]")


async def test_cancelling_before_the_executor_leaves_no_effect_at_all(
    runtime, db
) -> None:
    result = await runtime.runner.run(
        ToolTarget("note"),
        Action(tool="note", params={"text": "x"}),
        ExecutionContext(cancelled=lambda: True, runtime_profile=RuntimeProfile.TEST),
    )

    assert result.failure is Failure.CANCELLED
    assert result.disposition is EffectDisposition.NOT_STARTED
    assert effects_rows(db) == []


# --------------------------------------------------- the boundary was crossed


def test_whether_execution_started_is_a_fact_not_a_deduction() -> None:
    """`NOT_STARTED` and `UNKNOWN` differ by one thing only: did we call it.

    Reading that off the exception type would be guessing about the world — a
    `ConnectionError` is raised both by a socket that never opened and by one
    that closed after the message went out.
    """
    crashed = ExecutionOutput(ok=False, error="ConnectionError: reset", error_kind="crash")

    assert decide_disposition(
        executor_started=False, effectful=True, output=crashed
    ) is EffectDisposition.NOT_STARTED
    assert decide_disposition(
        executor_started=True, effectful=True, output=crashed
    ) is EffectDisposition.UNKNOWN
    # A read has nothing to apply, whether or not it got that far.
    assert decide_disposition(
        executor_started=True, effectful=False, output=crashed
    ) is EffectDisposition.NOT_APPLIED


def test_an_executor_may_prove_it_changed_nothing_but_never_by_raising() -> None:
    proven = ExecutionOutput(
        ok=False, error="odmowa serwera przed wysłaniem",
        disposition=EffectDisposition.NOT_APPLIED,
    )
    silent = ExecutionOutput(ok=False, error="odmowa serwera przed wysłaniem")

    assert decide_disposition(
        executor_started=True, effectful=True, output=proven
    ) is EffectDisposition.NOT_APPLIED
    # Identical wording, no declaration: the runner must not read the sentence.
    assert decide_disposition(
        executor_started=True, effectful=True, output=silent
    ) is EffectDisposition.UNKNOWN


async def test_a_missing_secret_never_reached_the_handler(runtime, db) -> None:
    result = await runtime.perform_tool("call_api", token="vault://nie_ma")

    assert not result.ok
    row = effects_rows(db)[0]
    assert row["disposition"] == EffectDisposition.NOT_STARTED.value


# ------------------------------------------------------- crash means unknown


async def test_an_effectful_crash_becomes_uncertain(capability_runner, db) -> None:
    async def boom(_: Invocation) -> Outcome:
        raise RuntimeError("bum")

    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender([], id="safety.boom", executor=boom))

    performed = await registry.perform("safety.boom", effect_id="boom-1")

    assert not performed.ok and performed.outcome.uncertain
    record = capability_runner.effects.load("boom-1")
    assert record is not None
    assert record.state is EffectState.UNCERTAIN
    assert record.disposition is EffectDisposition.UNKNOWN


async def test_an_effectful_crash_is_not_offered_for_another_try(runtime, db) -> None:
    """The agent loop retries on `retryable`. For effectful work that flag has to
    be false, or the repair budget becomes a duplicate-effect budget."""

    @runtime.registry.tool(
        "send_thing", "Zapisuje i pada.", params={},
        effects=[Effect.WRITE],
    )
    async def send_thing(ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("padło po zapisie")

    result = await runtime.perform(Action(tool="send_thing", task_id="t", step_key="k"))

    assert not result.ok
    assert not result.retryable, "efektu, który mógł zajść, nie ponawiamy sam z siebie"


async def test_a_read_that_failed_may_simply_be_read_again(runtime, db) -> None:
    """Nothing external to duplicate, so a fresh attempt is free and honest."""
    calls: list[int] = []

    @runtime.registry.tool("meter2", "Mierzy.", params={}, effects=[Effect.READ])
    async def meter2(ctx):  # type: ignore[no-untyped-def]
        calls.append(len(calls))
        if len(calls) < 2:
            raise RuntimeError("chwilowo nie")
        return {"reading": len(calls)}

    first = await runtime.perform(Action(tool="meter2", task_id="t", step_key="k"))
    assert not first.ok and first.retryable

    second = await runtime.perform(Action(tool="meter2", task_id="t", step_key="k"))
    assert second.ok and second.value == {"reading": 2}
    # Each read is its own invocation; nothing was replayed from the failure.
    ids = {r["effect_id"] for r in effects_rows(db)}
    assert len(ids) == 2


# ------------------------------------------------------- proven, so retryable


async def test_a_proven_harmless_failure_may_be_attempted_again(
    capability_runner, db
) -> None:
    runs: list[str] = []

    async def refused(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        if len(runs) == 1:
            return Outcome(
                ok=False, error="serwer odmówił przed wysłaniem",
                disposition=EffectDisposition.NOT_APPLIED,
            )
        return Outcome(ok=True, value={"sent": True}, evidence={"delivered": 1})

    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender([], id="safety.refused", executor=refused))

    first = await registry.perform("safety.refused", effect_id="ref-1")
    second = await registry.perform("safety.refused", effect_id="ref-1")

    assert not first.ok and second.ok
    assert len(runs) == 2, "dowiedziona porażka bez skutku wolno powtórzyć"


async def test_a_second_attempt_is_numbered_and_keeps_the_first_on_record(
    capability_runner, db
) -> None:
    """A retry that overwrote the previous attempt would erase the reason for it."""
    runs: list[str] = []

    async def refused(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        if len(runs) == 1:
            return Outcome(ok=False, error="pierwsza próba odrzucona",
                           disposition=EffectDisposition.NOT_APPLIED)
        return Outcome(ok=True, value={"sent": True}, evidence={"delivered": 1})

    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender([], id="safety.numbered", executor=refused))

    await registry.perform("safety.numbered", effect_id="num-1")
    await registry.perform("safety.numbered", effect_id="num-1")

    record = capability_runner.effects.load("num-1")
    assert record is not None
    assert record.attempt_number == 2
    assert len(record.attempts) == 1, "pierwsza próba musi zostać w historii"
    assert record.attempts[0]["disposition"] == EffectDisposition.NOT_APPLIED.value
    assert "pierwsza próba odrzucona" in record.attempts[0]["reason"]


async def test_both_attempts_stay_in_the_audit_trail(capability_runner, db) -> None:
    runs: list[str] = []

    async def refused(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        if len(runs) == 1:
            return Outcome(ok=False, error="nie tym razem",
                           disposition=EffectDisposition.NOT_APPLIED)
        return Outcome(ok=True, value={"sent": True}, evidence={"delivered": 1})

    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender([], id="safety.audited", executor=refused))

    await registry.perform("safety.audited", effect_id="aud-1")
    await registry.perform("safety.audited", effect_id="aud-1")

    rows = audit_rows(db, "safety.audited")
    assert len(rows) == 2, "audyt jest dopisywany, nie nadpisywany"
    assert rows[0]["detail"].startswith("[not_applied]")
    assert rows[1]["detail"].startswith("[applied]")


# ------------------------------------------- the measured failure, Defect B


def postcondition_failing(runs: list[str]) -> Capability:
    """Asked for 30%, measured 33%. It happened; it was not what was wanted."""

    async def run(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        return Outcome(ok=True, value={"requested": 30}, evidence={"volume_after": 33})

    async def measure(_: Invocation, outcome: Outcome) -> CapVerdict:
        after = outcome.evidence.get("volume_after")
        if after == 30:
            return CapVerdict(ok=True, checked=True, note="Ustawione i zmierzone.")
        return CapVerdict(
            ok=False, checked=True,
            note=f"Prosiłeś o 30%, zmierzyłem {after}%.",
            missing=("volume_after",),
        )

    return sender(
        [], id="safety.volume", executor=run, verifier=measure,
        evidence=(Field("volume_after", "int", "Zmierzona głośność"),),
    )


async def test_a_measured_miss_is_a_checked_failure_not_an_unknown(
    capability_runner, db
) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(postcondition_failing(runs))

    performed = await registry.perform("safety.volume", effect_id="vol-1")

    assert performed.ok, "wykonawca zwrócił wynik"
    assert not performed.verified, "ale cel nie został osiągnięty"
    assert performed.verdict.checked, "i ktoś to naprawdę zmierzył"
    assert not performed.outcome.uncertain, "to nie jest niewiedza, to pomiar"

    record = capability_runner.effects.load("vol-1")
    assert record is not None
    assert record.state is EffectState.DONE, "efekt zaszedł i jest rozliczony"
    assert record.disposition is EffectDisposition.APPLIED
    assert not record.repeatable


async def test_a_measured_miss_replays_as_the_same_failure_without_running_again(
    capability_runner, db
) -> None:
    """The regression this commit exists for.

    A DONE effect used to replay with `goal_met=True` inferred from its state,
    so "asked for 30%, measured 33%" came back the second time as a success.
    `DONE` means the record was settled truthfully, not that the user got what
    they wanted.
    """
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(postcondition_failing(runs))

    first = await registry.perform("safety.volume", effect_id="vol-2")
    second = await registry.perform("safety.volume", effect_id="vol-2")

    assert len(runs) == 1, "nie wolno wykonać efektu drugi raz"
    assert not second.verified, "sprawdzona porażka nie zmienia się w sukces"
    assert second.verdict.checked is first.verdict.checked
    assert second.verdict.ok is False


async def test_a_replayed_verdict_keeps_every_field_it_was_recorded_with(
    capability_runner, db
) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender(runs, id="safety.keep"))

    first = await capability_runner.run(
        _target("safety.keep"), Action(tool="safety.keep"),
        ExecutionContext(effect_id="keep-1", runtime_profile=RuntimeProfile.TEST),
    )
    second = await capability_runner.run(
        _target("safety.keep"), Action(tool="safety.keep"),
        ExecutionContext(effect_id="keep-1", runtime_profile=RuntimeProfile.TEST),
    )

    assert len(runs) == 1
    assert second.verification.checked_by == first.verification.checked_by
    assert second.verification.reason == first.verification.reason
    assert second.verification.evidence_ids == first.verification.evidence_ids
    assert second.replayed


async def test_an_applied_effect_cannot_be_reclaimed_for_another_go(
    capability_runner, db
) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(postcondition_failing(runs))

    await registry.perform("safety.volume", effect_id="vol-3")
    record = capability_runner.effects.load("vol-3")
    assert record is not None and not record.repeatable

    reservation = capability_runner.effects.reserve(
        "vol-3", capability_id="safety.volume"
    )
    assert not reservation.granted, "APPLIED nigdy nie wraca do puli"


async def test_a_successful_effect_replays_without_touching_the_executor(
    capability_runner
) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender(runs, id="safety.once"))

    first = await registry.perform("safety.once", effect_id="once-1")
    second = await registry.perform("safety.once", effect_id="once-1")

    assert len(runs) == 1
    assert second.outcome.value == first.outcome.value


async def test_an_uncertain_effect_refuses_to_be_replayed(capability_runner) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender(runs, id="safety.maybe"))
    capability_runner.effects.reserve("maybe-1", capability_id="safety.maybe")
    capability_runner.effects.mark_uncertain("maybe-1", "proces padł w trakcie")

    performed = await registry.perform("safety.maybe", effect_id="maybe-1")

    assert runs == []
    assert not performed.ok and performed.outcome.uncertain


# --------------------------------------------------------------- old rows


def test_an_effect_recorded_before_dispositions_existed_is_not_repeated(db) -> None:
    """A migrated row's external outcome genuinely is unknown — nobody wrote it
    down. Calling it repeatable to preserve the old behaviour would be inventing
    a fact about the world to avoid changing a rule."""
    from garis.kernel.effects import EffectStore

    db.execute(
        "INSERT INTO effects(effect_id, capability_id, state, arguments_hash,"
        " reserved_at) VALUES ('stary','chat.send','failed','abc',1.0)"
    )

    record = EffectStore(db).load("stary")

    assert record is not None
    assert record.state is EffectState.FAILED
    assert record.disposition is EffectDisposition.UNKNOWN
    assert not record.repeatable
    assert record.attempt_number == 1


def test_the_migration_keeps_every_row_it_found(db) -> None:
    db.execute(
        "INSERT INTO effects(effect_id, capability_id, state, arguments_hash,"
        " reserved_at, outcome) VALUES ('zachowany','x','done','h',2.0,'{\"a\": 1}')"
    )
    row = db.one("SELECT * FROM effects WHERE effect_id = 'zachowany'")
    assert row is not None and row["outcome"] == '{"a": 1}'


# ------------------------------------------------- ok is not success, ever


def test_a_structured_result_alone_cannot_make_a_report_verified() -> None:
    """`Outcome.ok` means the executor returned rather than crashed. That is all
    it has ever been allowed to mean."""
    goal = Goal(text="ustaw głośność na 30%")
    plan = Plan(steps=())
    evidence = [StepEvidence(tool="safety.volume", purpose="ustaw", ok=True)]

    report = build_report(
        goal, plan, evidence,
        Verification(goal_met=True, checked=False, reason="nikt nie sprawdził"),
    )

    assert not report.verified, "brak sprawdzenia to nie jest sukces"


def test_verified_is_exactly_the_three_facts_and_nothing_else() -> None:
    goal, plan = Goal(text="cel"), Plan(steps=())
    evidence = [StepEvidence(tool="t", purpose="p", ok=True)]

    for goal_met in (True, False):
        for checked in (True, False):
            for uncertain in (True, False):
                verification = Verification(
                    goal_met=goal_met, checked=checked, uncertain=uncertain
                )
                report = build_report(goal, plan, evidence, verification)
                assert report.verified == (goal_met and checked and not uncertain)


async def test_the_compatibility_facade_never_invents_a_verification(
    runtime
) -> None:
    """`note` has no postcondition. It may succeed; it may not be `verified`."""
    result = await runtime.perform_tool("note", text="x")

    assert result.ok
    assert result.verified is None, "brak sprawdzenia to nie jest True"


async def test_a_returned_result_with_an_unmet_goal_is_not_a_success(
    capability_runner
) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(postcondition_failing(runs))

    performed = await registry.perform("safety.volume", effect_id="vol-4")

    assert performed.outcome.ok is True
    assert performed.verified is False
    assert performed.to_dict()["verified"] is False


# ----------------------------------------------------------------- audit


async def test_the_audit_says_what_happened_to_the_world(capability_runner, db) -> None:
    runs: list[str] = []
    registry = bind(CapabilityRegistry(), capability_runner)
    registry.add(sender(runs, id="safety.audit"))

    await registry.perform("safety.audit", effect_id="a-1")

    row = audit_rows(db, "safety.audit")[0]
    assert row["detail"].startswith("[applied]")


def _target(name: str):
    from garis.kernel.contracts import CapabilityTarget

    return CapabilityTarget(name)
