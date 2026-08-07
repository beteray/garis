"""After a crash, GARIS looks. It does not guess and it does not repeat.

Etap D. An effect left `UNCERTAIN` by a dead process is the one case where
doing nothing is safe and doing the obvious thing — running it again — is how
one payment becomes two. Recovery is the third option: inspect the world with a
read-only capability, through the same envelope as everything else, and write
down what was actually seen.

The distinction these tests exist to defend is the one that is easiest to lose:

    audio.set(volume=30) → crash → audio.get() == 30

proves the volume is 30. It does not prove GARIS set it — a person can turn a
dial while the engine is down. Two truths, two fields.
"""

from __future__ import annotations

import json

import pytest

from garis.capabilities import fixtures
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.registry import REGISTRY, CapabilityRegistry
from garis.errors import ConfigError, GarisError
from garis.kernel.contracts import EffectDisposition
from garis.kernel.effects import EffectState, EffectStore
from garis.kernel.outbox import EventOutbox
from garis.kernel.recovery import RecoveryStatus, RecoveryStore
from garis.runtime import AuditLog, TargetResolver, ToolRegistry
from garis.runtime.recovery import EffectRecoveryService


@pytest.fixture(autouse=True)
def world():
    fixtures.reset()
    yield fixtures.WORLD
    fixtures.reset()


@pytest.fixture
def catalogue(capability_runner) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in fixtures.ALL:
        registry.add(capability)
        # `evidence_verifier` resolves the capability through the shared
        # catalogue, so a local-only registration would come back "unchecked"
        # and every verdict here would be a false negative.
        REGISTRY.add(capability)
    capability_runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(capability_runner)
    yield registry
    for capability in fixtures.ALL:
        REGISTRY._by_id.pop(capability.id, None)


@pytest.fixture
def service(capability_runner, catalogue, db, bus) -> EffectRecoveryService:
    return EffectRecoveryService(
        effects=EffectStore(db),
        recoveries=RecoveryStore(db),
        runner=capability_runner,
        resolver=TargetResolver(ToolRegistry(), catalogue),
        capabilities=catalogue,
        audit=AuditLog(db),
        outbox=EventOutbox(db, bus),
    )


def crashed(db, capability_id: str, *, effect_id: str = "eff-1", wanted: int = 30):
    """The footprint of a process that died mid-action: reserved, never settled."""
    effects = EffectStore(db)
    effects.reserve(effect_id, capability_id=capability_id, task_id="t1",
                    step_key="set", args={"level": wanted})
    effects.sweep_unsettled()
    with db.transaction() as conn:
        conn.execute("UPDATE effects SET outcome = ? WHERE effect_id = ?",
                     (json.dumps({"requested": wanted}), effect_id))
    return effects.load(effect_id)


# ------------------------------------------------------- the original is safe


async def test_recovery_never_runs_the_original_operation_again(service, db) -> None:
    """The whole point. Anything else here is detail."""
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    ran: list[str] = []
    original = fixtures.SET_LEVEL.executor

    async def watched(invocation):
        ran.append(invocation.capability)
        return await original(invocation)

    object.__setattr__(fixtures.SET_LEVEL, "executor", watched)
    try:
        await service.reconcile("eff-1")
    finally:
        object.__setattr__(fixtures.SET_LEVEL, "executor", original)

    assert ran == [], "recovery uruchomiło operację, którą miało tylko obejrzeć"


async def test_the_inspection_goes_through_the_one_envelope(service, db, capability_runner) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    entered: list[str] = []
    original = capability_runner.run

    def watched(target, action, context=None):
        entered.append(target.name)
        return original(target, action, context)

    capability_runner.run = watched  # type: ignore[method-assign]
    await service.reconcile("eff-1")

    assert entered == ["fixture.level.read"]


async def test_the_inspection_is_a_child_of_the_effect_it_explains(
    service, db, capability_runner
) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    seen: list[tuple[str, str, int]] = []
    original = capability_runner.run

    def watched(target, action, context=None):
        seen.append((context.parent_effect_id, context.step_key, context.depth))
        return original(target, action, context)

    capability_runner.run = watched  # type: ignore[method-assign]
    await service.reconcile("eff-1")

    parent, step_key, depth = seen[0]
    assert parent == "eff-1", "drzewo efektów musi dać się odczytać jako drzewo"
    assert step_key != "set", "własny klucz kroku, żeby efekty się nie zderzyły"
    assert depth == 1


async def test_the_inspection_gets_a_fresh_effect_id(service, db) -> None:
    """Not derived, and not by special-casing — a read declares no effects, so
    the existing rule already gives it a new id. Replaying a stored measurement
    would hand back the reading from before the crash."""
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    outcome = await service.reconcile("eff-1")

    assert outcome.record is not None
    assert outcome.record.inspector_effect_id.startswith("inv-")
    assert outcome.record.inspector_effect_id != "eff-1"


async def test_the_inspection_leaves_its_own_audit_row(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    await service.reconcile("eff-1")

    tools = [r["tool"] for r in db.query("SELECT tool FROM audit ORDER BY id")]
    assert "fixture.level.read" in tools


async def test_recovery_publishes_its_own_events(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    await service.reconcile("eff-1")

    topics = [r["topic"] for r in db.query("SELECT topic FROM event_outbox ORDER BY sequence")]
    assert "effect.recovery.started" in topics
    assert "effect.recovery.resolved" in topics


# ------------------------------------------- the two truths, kept apart


async def test_a_matching_state_proves_the_goal_and_not_the_cause(service, db) -> None:
    """The correction this whole phase turns on.

    Asked for 30, the world reads 30 — but a person could have set it while the
    engine was down. The goal is verified; causality is not, and must not be
    upgraded to APPLIED because the number happens to match.
    """
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert outcome.disposition is EffectDisposition.UNKNOWN, "nie wiemy, kto to ustawił"
    assert outcome.verification.goal_met and outcome.verification.checked
    assert not outcome.verification.uncertain
    assert outcome.goal_met, "zadanie może się na tym zakończyć"

    effect = EffectStore(db).load("eff-1")
    assert effect.state is EffectState.UNCERTAIN, "księga efektów zapisuje, co zrobiliśmy"
    assert effect.disposition is EffectDisposition.UNKNOWN


async def test_a_trace_of_the_original_act_is_what_earns_applied(service, db) -> None:
    """APPLIED needs evidence tied to the operation, not a matching value."""
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30
    fixtures.WORLD["receipt"] = "eff-1"

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.RESOLVED_APPLIED
    assert outcome.disposition is EffectDisposition.APPLIED
    assert EffectStore(db).load("eff-1").state is EffectState.DONE


async def test_applied_but_missed_is_a_checked_failure(service, db) -> None:
    """Asked for 30, measured 33. It happened, and it is not what was wanted."""
    crashed(db, "fixture.level.set_missed")
    fixtures.WORLD["level"] = 33

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.RESOLVED_APPLIED
    assert outcome.disposition is EffectDisposition.APPLIED
    assert not outcome.verification.goal_met
    assert outcome.verification.checked and not outcome.verification.uncertain

    effect = EffectStore(db).load("eff-1")
    assert effect.state is EffectState.DONE
    assert effect.verification["goal_met"] is False
    assert not effect.repeatable, "sprawdzona porażka nie jest wolna do ponowienia"


async def test_a_measured_failure_can_never_read_back_as_a_verified_success(service, db) -> None:
    crashed(db, "fixture.level.set_missed")
    fixtures.WORLD["level"] = 33
    await service.reconcile("eff-1")

    stored = EffectStore(db).load("eff-1")

    assert stored.verification["goal_met"] is False
    assert stored.verification["checked"] is True
    assert not (stored.state is EffectState.DONE and stored.verification["goal_met"])


# ---------------------------------------------------- nothing happened at all


async def test_an_untouched_world_resolves_to_not_applied(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 5  # nothing like what was asked for

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.RESOLVED_NOT_APPLIED
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert EffectStore(db).load("eff-1").state is EffectState.FAILED


async def test_a_recovered_not_applied_still_needs_a_person_before_a_retry(service, db) -> None:
    """Honest, and second-hand.

    `reserve_in` re-claims anything `repeatable` returns True for, so this flag
    living inside that property is the only thing that actually holds. An
    operation that was uncertain once does not get repeated by a resume.
    """
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 5
    await service.reconcile("eff-1")

    effect = EffectStore(db).load("eff-1")

    assert effect.disposition.permits_retry, "dyspozycja mówi, że świat się nie ruszył"
    assert effect.retry_requires_confirmation
    assert not effect.repeatable, "ale nikt nie ponowi tego bez decyzji człowieka"


async def test_a_recovered_not_applied_is_not_reclaimed_by_the_next_reservation(
    service, db
) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 5
    await service.reconcile("eff-1")

    again = EffectStore(db).reserve(
        "eff-1", capability_id="fixture.level.set", task_id="t1",
        step_key="set", args={"level": 30},
    )

    assert not again.granted, "wznowione zadanie wykonałoby operację po raz drugi"


async def test_an_ordinary_not_applied_keeps_the_old_retry_rule(db) -> None:
    """Only recovery raises the gate. A failure observed as it happened is free."""
    effects = EffectStore(db)
    effects.reserve("eff-2", capability_id="fixture.level.set", args={"level": 1})
    effects.complete("eff-2", ok=False, disposition=EffectDisposition.NOT_APPLIED)

    assert effects.load("eff-2").repeatable


# ------------------------------------------------------- when nothing is known


async def test_an_inspection_that_settles_nothing_leaves_the_effect_alone(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = None  # the reading itself fails

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.STILL_UNKNOWN
    effect = EffectStore(db).load("eff-1")
    assert effect.state is EffectState.UNCERTAIN
    assert effect.disposition is EffectDisposition.UNKNOWN
    assert not effect.repeatable


async def test_a_capability_with_no_reconciler_says_so(service, db) -> None:
    crashed(db, "fixture.level.set_blind")

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.MANUAL_REQUIRED
    assert "sam" in outcome.reason.lower() or "ocenić" in outcome.reason
    assert EffectStore(db).load("eff-1").state is EffectState.UNCERTAIN


# ---------------------------------------------------------- unsafe inspectors


@pytest.mark.parametrize(
    "capability_id",
    ["fixture.level.set_unsafe", "fixture.level.set_unverifiable",
     "fixture.level.set_elsewhere"],
)
async def test_an_unsafe_inspector_is_refused_before_anything_runs(
    service, db, capability_runner, capability_id
) -> None:
    crashed(db, capability_id)
    fixtures.WORLD["level"] = 30

    entered: list[str] = []
    capability_runner.run = lambda *a, **k: entered.append(a[0].name)  # type: ignore[method-assign]

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.MANUAL_REQUIRED
    assert entered == [], "odmowa musi paść przed wykonaniem, nie po nim"


async def test_the_envelope_decides_about_approval_not_the_service(service, db) -> None:
    """Correction 2: no second policy engine.

    The inspector needs a person. The service does not work that out from
    `needs_approval` — it runs the inspection through the runner and reads the
    runner's answer. `PolicyEngine`'s conditions live in one place.
    """
    crashed(db, "fixture.level.set_approving")
    fixtures.WORLD["level"] = 30

    outcome = await service.reconcile("eff-1")

    assert outcome.status is RecoveryStatus.MANUAL_REQUIRED
    assert EffectStore(db).load("eff-1").state is EffectState.UNCERTAIN


async def test_an_empty_evidence_schema_does_not_disqualify_an_inspector(
    service, db, catalogue
) -> None:
    """Correction 3: `Verification` is authoritative, not the evidence count.

    A capability may return a checked structured reading without minting a
    separate evidence id, and refusing that would rule out honest readings on a
    technicality.
    """
    assert service.can_recover(crashed(db, "fixture.level.set"))
    assert catalogue.get("fixture.level.read").evidence, "ta akurat deklaruje dowody"

    # The one that genuinely cannot verify is refused — for saying so, not for
    # having an empty schema.
    assert not service.can_recover(crashed(db, "fixture.level.set_unverifiable",
                                           effect_id="eff-9"))


# --------------------------------------------------------------- the history


async def test_recovery_history_is_append_only_and_numbered(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = None
    await service.reconcile("eff-1")
    await service.reconcile("eff-1")
    fixtures.WORLD["level"] = 30
    await service.reconcile("eff-1")

    history = RecoveryStore(db).history("eff-1")

    assert [r.attempt_number for r in history] == [1, 2, 3]
    assert [r.status for r in history] == [
        RecoveryStatus.STILL_UNKNOWN,
        RecoveryStatus.STILL_UNKNOWN,
        RecoveryStatus.RESOLVED_GOAL_ONLY,
    ]
    assert len({r.recovery_id for r in history}) == 3


async def test_the_history_records_the_canonical_inspector(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30
    await service.reconcile("eff-1")

    assert RecoveryStore(db).latest("eff-1").inspector == "fixture.level.read"


async def test_the_verdict_replays_exactly_as_it_was_measured(service, db) -> None:
    crashed(db, "fixture.level.set_missed")
    fixtures.WORLD["level"] = 33
    outcome = await service.reconcile("eff-1")

    stored = RecoveryStore(db).latest("eff-1")

    assert stored.status is outcome.status
    assert stored.disposition is outcome.disposition
    assert stored.verification == outcome.verification.to_dict()


async def test_settlement_history_and_event_land_together(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 5

    broken = EffectStore(db).settle_in

    def explode(*args, **kwargs):
        broken(*args, **kwargs)
        raise RuntimeError("dysk padł w środku zapisu")

    service.effects.settle_in = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.reconcile("eff-1")

    effect = EffectStore(db).load("eff-1")
    assert effect.state is EffectState.UNCERTAIN, "efekt nietknięty"
    assert RecoveryStore(db).history("eff-1") == [], "brak połowicznej historii"


async def test_events_carry_codes_and_flags_but_never_what_was_read(service, db) -> None:
    crashed(db, "fixture.level.set")
    fixtures.WORLD["level"] = 30
    await service.reconcile("eff-1")

    rows = db.query("SELECT payload FROM event_outbox WHERE topic = ?",
                    ("effect.recovery.resolved",))
    payload = json.loads(rows[0]["payload"])

    assert set(payload) == {
        "recovery_id", "effect_id", "attempt_number", "status", "disposition",
        "inspector", "goal_met", "checked", "uncertain", "evidence_ids",
    }
    # The key set *is* the guarantee — a payload can only leak what it carries.
    # Spot-checked as well: the measured number itself never travels. Identifiers
    # do (`fixture.level.read`, `ev-level`), which is the point of ids.
    assert 30 not in payload.values(), "odczytana wartość nie jedzie na magistralę"


# ----------------------------------------------------------------- edge cases


async def test_an_effect_that_is_not_uncertain_is_left_alone(service, db) -> None:
    effects = EffectStore(db)
    effects.reserve("eff-3", capability_id="fixture.level.set", args={})
    effects.complete("eff-3", ok=True)

    outcome = await service.reconcile("eff-3")

    assert outcome.status is RecoveryStatus.NOT_RECOVERABLE


async def test_an_unknown_effect_is_an_error_not_a_verdict(service) -> None:
    with pytest.raises(GarisError):
        await service.reconcile("eff-nie-ma")


# ----------------------------------------------------------------- structural


def test_the_service_owns_no_execution_of_its_own() -> None:
    """It may orchestrate. It may not reach past the envelope."""
    import ast
    import inspect

    from garis.runtime import recovery

    tree = ast.parse(inspect.getsource(recovery))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)

    for forbidden in (".executor(", "capability.executor", "Runtime.perform",
                      "bus.emit", "subprocess", "LegacyToolExecutor"):
        assert forbidden not in code, f"recovery sięgnęło poza kopertę: {forbidden}"


def test_the_synthetic_capabilities_cannot_reach_production() -> None:
    from garis import profiles
    from garis.kernel.contracts import RuntimeProfile

    contents = profiles.inspect(RuntimeProfile.PRODUCTION, capabilities=fixtures.ALL)

    with pytest.raises(ConfigError):
        profiles.validate(contents)


def test_every_synthetic_capability_is_marked_as_one() -> None:
    assert all(c.fixture for c in fixtures.ALL)
