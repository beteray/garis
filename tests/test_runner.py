"""One envelope, entered once, whatever is being run.

GARIS used to have two ways to make something happen. `Runtime.perform` had
policy, approvals and an audit trail but no effect identity, no evidence and no
verifier; `CapabilityRegistry.perform` had typed evidence and a mandatory
verifier but no policy, no audit and an idempotence dict a restart erased. Every
guarantee was true of half the system, and which half depended on how the work
happened to be spelled.

These tests hold the merged path to the promises neither half could make alone:
one policy decision, one audit row, one effect per invocation; a denial that
leaves no trace in the world; a success that is never claimed before its
evidence has committed; and a production process that cannot be running on a
stub.
"""

from __future__ import annotations

import pytest

from garis import profiles
from garis.capabilities.base import (
    Capability,
    Field,
    Invocation,
    Outcome,
    Permission,
    Risk,
    evidence_verifier,
)
from garis.capabilities.base import (
    Verdict as CapVerdict,
)
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.registry import CapabilityRegistry
from garis.errors import ApprovalRequired, ConfigError, PolicyDenied, StoreError
from garis.kernel.contracts import (
    CapabilityTarget,
    Effect,
    ExecutionContext,
    Failure,
    RuntimeProfile,
    ToolTarget,
)
from garis.kernel.effects import EffectState
from garis.runtime.action import Action
from garis.runtime.policy import PolicyEngine
from garis.runtime.runner import derive_effect_id

# --------------------------------------------------------------- narrow doubles


class CountingPolicy(PolicyEngine):
    """The real rules, with a tally. Counting a stub's calls would prove nothing
    about the engine that actually runs in production."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.evaluations = 0

    def evaluate_subject(self, subject, action, estimate=None):
        self.evaluations += 1
        return super().evaluate_subject(subject, action, estimate)


def counting(runner) -> CountingPolicy:
    """Swap the runner's policy for one that counts, keeping the same rules."""
    tally = CountingPolicy(runner.policy.autonomy, runner.policy.paths)
    runner.policy = tally
    return tally


def spy_reservations(runner) -> list[str]:
    """Every effect id the runner actually claimed."""
    claimed: list[str] = []
    original = runner.effects.reserve_in

    def watched(conn, effect_id, **kwargs):
        claimed.append(effect_id)
        return original(conn, effect_id, **kwargs)

    runner.effects.reserve_in = watched  # type: ignore[method-assign]
    return claimed


def audit_rows(db, tool: str) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT * FROM audit WHERE tool = ? ORDER BY id", (tool,)
    )]


def capability(runs: list[str], **over) -> Capability:
    async def run(invocation: Invocation) -> Outcome:
        runs.append(invocation.capability)
        return Outcome(ok=True, value={"done": True}, evidence={"seen": 1})

    base = dict(
        id="runner.thing", version=1, summary="Robi rzecz.", risk=Risk.READ,
        permission=Permission.NONE, executor=run, verifier=evidence_verifier(),
        evidence=(Field("seen", "int", "Co zmierzono"),),
    )
    return Capability(**{**base, **over})


def catalogue(runner, *caps) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for cap in caps:
        registry.add(cap)
    runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(runner)
    return registry


async def run_capability(runner, registry, name, args=None, effect_id=""):
    return await runner.run(
        CapabilityTarget(name),
        Action(tool=name, params=dict(args or {})),
        ExecutionContext(effect_id=effect_id, runtime_profile=RuntimeProfile.TEST),
    )


# ------------------------------------------------------- one envelope, entered


async def test_a_tool_and_a_capability_are_run_by_the_same_machinery(
    runtime, capability_runner
) -> None:
    """The point of the whole commit: not two paths that agree, one path."""
    runs: list[str] = []
    registry = catalogue(capability_runner, capability(runs))

    entered: list[str] = []
    for runner in (runtime.runner, capability_runner):
        original = runner.run

        def watched(target, action, context=None, _original=original):
            entered.append(type(target).__name__)
            return _original(target, action, context)

        runner.run = watched  # type: ignore[method-assign]

    await runtime.perform_tool("note", text="x")
    await registry.perform("runner.thing")

    assert entered == ["ToolTarget", "CapabilityTarget"], (
        "obie drogi muszą wejść do tej samej koperty"
    )


async def test_runtime_perform_decides_nothing_itself(runtime) -> None:
    """The façade builds a target, calls the runner and translates the answer.

    Asserted against the source because the guarantee is structural: the moment
    `perform` grows its own policy call again, there are two paths whether or not
    any single test notices.
    """
    import inspect

    from garis.runtime.executor import Runtime

    body = inspect.getsource(Runtime.perform)
    for forbidden in ("self.policy", "self.audit", "self.approvals", "self.effects",
                      "self.leases", "self.bus.emit", "self.outbox"):
        assert forbidden not in body, f"perform robi coś samo: {forbidden}"
    assert "self.runner.run" in body


async def test_a_legacy_tool_is_judged_once(runtime) -> None:
    tally = counting(runtime.runner)
    await runtime.perform_tool("note", text="x")
    assert tally.evaluations == 1


async def test_a_capability_is_judged_once(capability_runner) -> None:
    runs: list[str] = []
    registry = catalogue(capability_runner, capability(runs))
    tally = counting(capability_runner)

    await registry.perform("runner.thing")

    assert tally.evaluations == 1


async def test_every_invocation_leaves_exactly_one_audit_row(runtime, db) -> None:
    await runtime.perform_tool("note", text="x")
    assert len(audit_rows(db, "note")) == 1

    await runtime.perform_tool("note", text="y")
    assert len(audit_rows(db, "note")) == 2


async def test_a_nested_call_is_audited_as_its_own_invocation(runtime, db) -> None:
    """One row for the parent and one for each real child — not one for the tree."""

    @runtime.registry.tool("outer", "Woła odczyt.", params={}, effects=[Effect.READ])
    async def outer(ctx):  # type: ignore[no-untyped-def]
        return await ctx.perform("look", what="wnętrze")

    await runtime.perform_tool("outer")

    assert len(audit_rows(db, "outer")) == 1
    assert len(audit_rows(db, "look")) == 1


async def test_a_nested_call_gets_its_own_step_and_effect(runtime, db) -> None:
    """Sharing the parent's step key would make the two effects collide, and a
    resumed task would then replay one in place of the other."""

    @runtime.registry.tool("outer2", "Woła odczyt.", params={}, effects=[Effect.READ])
    async def outer2(ctx):  # type: ignore[no-untyped-def]
        return await ctx.perform("look", what="wnętrze")

    await runtime.perform(Action(tool="outer2", task_id="t1", step_key="krok-1"))

    keys = {r["step_key"] for r in db.query("SELECT step_key FROM effects")}
    assert "krok-1" in keys
    assert any(k.startswith("krok-1/look#") for k in keys), keys


# ------------------------------------------------------------- denial is clean


async def test_a_policy_refusal_reaches_neither_the_executor_nor_the_world(
    runtime, paths, db
) -> None:
    claimed = spy_reservations(runtime.runner)

    with pytest.raises(PolicyDenied):
        await runtime.perform_tool("note", text=str(paths.key_file))

    assert claimed == [], "odmowa nie może zarezerwować efektu"
    assert db.query("SELECT * FROM effects") == []
    rows = audit_rows(db, "note")
    assert len(rows) == 1 and rows[0]["outcome"] == "denied"


async def test_a_refused_approval_reaches_neither_the_executor_nor_the_world(
    runtime, db
) -> None:
    with pytest.raises(ApprovalRequired) as caught:
        await runtime.perform_tool("publish", text="wpis")
    runtime.approvals.resolve(caught.value.request_id, False)

    claimed = spy_reservations(runtime.runner)
    from garis.errors import ApprovalDenied

    with pytest.raises(ApprovalDenied):
        await runtime.perform_tool("publish", text="wpis")

    assert claimed == [], "odmowa użytkownika nie może zarezerwować efektu"
    assert db.query("SELECT * FROM effects") == []
    assert [r["outcome"] for r in audit_rows(db, "publish")] == [
        "awaiting_approval", "rejected_by_user",
    ]


async def test_being_cancelled_first_means_nothing_happened(runtime, db) -> None:
    claimed = spy_reservations(runtime.runner)

    result = runtime.runner.run(
        ToolTarget("note"),
        Action(tool="note", params={"text": "x"}),
        ExecutionContext(cancelled=lambda: True, runtime_profile=RuntimeProfile.TEST),
    )
    outcome = await result

    assert outcome.failure is Failure.CANCELLED
    assert claimed == []
    assert db.query("SELECT * FROM effects") == []
    assert [r["outcome"] for r in audit_rows(db, "note")] == ["cancelled"]


# ------------------------------------------------------------- effect identity


async def test_the_same_effectful_step_resolves_to_the_same_identity() -> None:
    """A task resumed after a crash must land on its own reservation."""
    from garis.runtime.policy import subject_from_tool
    from garis.runtime.registry import ParamSpec, ToolRegistry

    reg = ToolRegistry()

    @reg.tool("send", "Wysyła.", params={"to": ParamSpec("string", required=True)},
              effects=[Effect.SEND_MESSAGE])
    async def send(ctx, to):  # type: ignore[no-untyped-def]
        return {"sent": to}

    subject = subject_from_tool(reg.get("send"))
    first = Action(tool="send", params={"to": "ala"}, task_id="t", step_key="k1")
    again = Action(tool="send", params={"to": "ala"}, task_id="t", step_key="k1")
    other = Action(tool="send", params={"to": "ola"}, task_id="t", step_key="k1")

    assert derive_effect_id("send", first, subject) == derive_effect_id("send", again, subject)
    assert derive_effect_id("send", first, subject) != derive_effect_id("send", other, subject)


async def test_a_repeated_reading_measures_again_instead_of_replaying(runtime) -> None:
    """Replaying a measurement hands back the disk figure from ten minutes ago
    dressed as the disk figure now."""
    seen: list[int] = []

    @runtime.registry.tool("meter", "Mierzy.", params={}, effects=[Effect.READ])
    async def meter(ctx):  # type: ignore[no-untyped-def]
        seen.append(len(seen))
        return {"reading": len(seen)}

    step = Action(tool="meter", task_id="t", step_key="k")
    first = await runtime.perform(step)
    second = await runtime.perform(Action(tool="meter", task_id="t", step_key="k"))

    assert first.value == {"reading": 1}
    assert second.value == {"reading": 2}, "odczyt musi być świeży, nie odtworzony"


async def test_a_finished_effect_is_replayed_rather_than_done_again(
    capability_runner
) -> None:
    runs: list[str] = []
    registry = catalogue(
        capability_runner,
        capability(runs, id="runner.once", risk=Risk.REVERSIBLE,
                   effects=frozenset({Effect.WRITE})),
    )

    first = await registry.perform("runner.once", effect_id="once-1")
    second = await registry.perform("runner.once", effect_id="once-1")

    assert len(runs) == 1, "drugi przebieg wykonałby efekt jeszcze raz"
    assert second.outcome.value == first.outcome.value


async def test_the_same_identity_for_different_arguments_is_refused(
    capability_runner
) -> None:
    """A reused id would hand back someone else's result, or act under a used
    identity. Neither is acceptable, so nothing runs."""
    runs: list[str] = []
    registry = catalogue(
        capability_runner,
        capability(runs, id="runner.args", effects=frozenset({Effect.WRITE}),
                   inputs=(Field("level", "int", "Poziom"),)),
    )

    await registry.perform("runner.args", {"level": 1}, effect_id="shared")
    performed = await registry.perform("runner.args", {"level": 2}, effect_id="shared")

    assert not performed.ok
    assert len(runs) == 1, "drugie wywołanie nie mogło dotknąć wykonawcy"


async def test_an_uncertain_effect_is_never_quietly_repeated(
    capability_runner, db
) -> None:
    """Retrying something that might have worked is how one payment becomes two."""
    runs: list[str] = []
    registry = catalogue(
        capability_runner,
        capability(runs, id="runner.maybe", effects=frozenset({Effect.WRITE})),
    )
    capability_runner.effects.reserve("maybe-1", capability_id="runner.maybe")
    capability_runner.effects.mark_uncertain("maybe-1", "proces padł w trakcie")

    performed = await registry.perform("runner.maybe", effect_id="maybe-1")

    assert runs == [], "nie wolno powtarzać czegoś, co mogło się wydarzyć"
    assert not performed.ok
    assert performed.outcome.uncertain


async def test_a_crash_mid_effect_is_recorded_as_uncertain(capability_runner, db) -> None:
    async def boom(_: Invocation) -> Outcome:
        raise RuntimeError("bum")

    registry = catalogue(
        capability_runner,
        capability([], id="runner.boom", executor=boom, risk=Risk.REVERSIBLE,
                   effects=frozenset({Effect.WRITE})),
    )

    performed = await registry.perform("runner.boom", effect_id="boom-1")

    assert not performed.ok and performed.outcome.uncertain
    record = capability_runner.effects.load("boom-1")
    assert record is not None and record.state is EffectState.UNCERTAIN
    assert not record.repeatable


async def test_a_crash_in_a_reading_is_a_plain_failure(capability_runner) -> None:
    """Nothing changed, so "I don't know" would be its own kind of untruth."""

    async def boom(_: Invocation) -> Outcome:
        raise RuntimeError("bum")

    registry = catalogue(
        capability_runner, capability([], id="runner.readboom", executor=boom)
    )

    performed = await registry.perform("runner.readboom", effect_id="rb-1")

    assert not performed.ok and not performed.outcome.uncertain
    record = capability_runner.effects.load("rb-1")
    assert record is not None and record.state is EffectState.FAILED
    assert record.repeatable


# --------------------------------------------------------------- truthfulness


async def test_a_failed_postcondition_never_becomes_verified(capability_runner) -> None:
    async def unproven(_: Invocation) -> Outcome:
        return Outcome(ok=True, value={"done": True}, evidence={})  # nothing measured

    registry = catalogue(
        capability_runner, capability([], id="runner.silent", executor=unproven)
    )

    performed = await registry.perform("runner.silent")

    assert performed.ok, "wykonanie się powiodło"
    assert not performed.verified, "ale nic tego nie potwierdziło"


async def test_a_verifier_that_refuses_cannot_be_talked_round(capability_runner) -> None:
    async def says_no(_: Invocation, __: Outcome) -> CapVerdict:
        return CapVerdict(ok=False, checked=True, note="Sprawdziłem i nie wyszło.")

    registry = catalogue(
        capability_runner,
        capability([], id="runner.refused", verifier=says_no, evidence=()),
    )

    performed = await registry.perform("runner.refused")

    assert not performed.verified
    assert performed.verdict.checked


async def test_a_tool_with_no_postcondition_is_finished_but_not_checked(
    runtime
) -> None:
    """A legacy tool proves nothing about its own goal, and says so."""
    result = await runtime.runner.run(
        ToolTarget("note"), Action(tool="note", params={"text": "x"}),
        ExecutionContext(runtime_profile=RuntimeProfile.TEST),
    )

    assert result.ok
    assert not result.verification.checked
    assert not result.verified


# --------------------------------------------------------------- one commit


async def test_a_settlement_and_its_event_land_together_or_not_at_all(
    capability_runner, db
) -> None:
    """An announcement about something that did not commit is the same family of
    untruth as a stub reporting "sprawdzone"."""
    registry = catalogue(
        capability_runner,
        capability([], id="runner.atomic", effects=frozenset({Effect.WRITE})),
    )

    original = capability_runner.outbox.stage
    staged: list[str] = []

    def refuse(conn, topic, **kwargs):
        # The reservation and its "started" event commit normally; the
        # settlement transaction is the one that dies.
        staged.append(topic)
        if len(staged) > 1:
            raise StoreError("dysk pełny")
        return original(conn, topic, **kwargs)

    capability_runner.outbox.stage = refuse  # type: ignore[method-assign]

    performed = await registry.perform("runner.atomic", effect_id="atom-1")

    assert not performed.ok
    assert performed.outcome.uncertain, "wykonało się, ale nie da się tego zapisać"
    # The reservation never got its settlement, which is exactly what the
    # startup sweep reads as "nobody knows what happened".
    topics = [r["topic"] for r in db.query("SELECT topic FROM event_outbox")]
    assert topics == ["action.started"], "zdarzenie o rozliczeniu, które nie przeszło"
    record = capability_runner.effects.load("atom-1")
    assert record is not None and record.state is EffectState.RESERVED


async def test_an_event_is_published_only_after_its_fact_committed(
    runtime, db, bus
) -> None:
    sub = bus.subscribe("action.started", "action.finished")
    await runtime.perform_tool("note", text="x")

    published = [e.topic for e in bus.recent("action.started")] + [
        e.topic for e in bus.recent("action.finished")
    ]
    assert "action.started" in published and "action.finished" in published

    rows = db.query("SELECT topic, published_at FROM event_outbox ORDER BY sequence")
    assert [r["topic"] for r in rows] == ["action.started", "action.finished"]
    assert all(r["published_at"] is not None for r in rows), "opublikowane po zapisie"
    sub.close()


# ------------------------------------------------------------------- profiles


def test_production_refuses_to_be_built_on_a_stub(home) -> None:
    from garis import app as garis_app

    with pytest.raises(ConfigError):
        garis_app.build(include_fake=True, profile=RuntimeProfile.PRODUCTION)


def test_development_refuses_a_stub_too(home) -> None:
    """A development process that silently behaves like a fixture is how a
    stub's cheerful verdict gets mistaken for a real one."""
    from garis import app as garis_app

    with pytest.raises(ConfigError):
        garis_app.build(include_fake=True, profile=RuntimeProfile.DEVELOPMENT)


def test_development_contains_no_doubles_by_default(home) -> None:
    from garis import app as garis_app

    instance = garis_app.build(profile=RuntimeProfile.DEVELOPMENT)
    try:
        assert instance.contents is not None
        assert instance.contents.doubles == ()
        assert instance.contents.fixtures == ()
    finally:
        instance.close()


def test_a_production_process_names_what_it_contains(home) -> None:
    """Identities, not counts: "three providers" is true of a healthy install
    and of one that quietly picked up a fake."""
    from garis import app as garis_app

    instance = garis_app.build(profile=RuntimeProfile.PRODUCTION)
    try:
        contents = instance.contents
        assert contents is not None
        assert "FakeProvider" not in contents.providers
        assert contents.doubles == ()
        assert contents.fixtures == ()
        assert contents.profile is RuntimeProfile.PRODUCTION
    finally:
        instance.close()


def test_a_fixture_capability_keeps_production_from_starting() -> None:
    stub = capability([], id="runner.fixture", fixture=True)

    contents = profiles.inspect(RuntimeProfile.PRODUCTION, capabilities=[stub])

    assert contents.fixtures == ("runner.fixture",)
    with pytest.raises(ConfigError):
        profiles.validate(contents)


def test_a_stub_provider_keeps_production_from_starting() -> None:
    from garis.models.providers.fake import FakeProvider

    contents = profiles.inspect(RuntimeProfile.PRODUCTION, providers=[FakeProvider()])

    assert contents.doubles == ("FakeProvider",)
    with pytest.raises(ConfigError):
        profiles.validate(contents)


def test_tests_may_hold_doubles_and_can_see_which(home) -> None:
    from garis import app as garis_app

    instance = garis_app.build(include_fake=True, profile=RuntimeProfile.TEST)
    try:
        assert instance.contents is not None
        assert "FakeProvider" in instance.contents.providers
        assert instance.contents.doubles == ("FakeProvider",)
    finally:
        instance.close()


async def test_the_command_line_asks_for_production_in_so_many_words(
    monkeypatch, home
) -> None:
    """The engine the desktop shell starts is this process, so this is the one
    that must not be able to answer with a stub."""
    import argparse

    from garis import cli

    chosen: dict[str, object] = {}

    def fake_build(home_arg=None, **kwargs):
        chosen.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "build", fake_build)
    args = argparse.Namespace(home=None, handler=None)
    with pytest.raises(SystemExit):
        await cli._run(args)

    assert chosen["profile"] is RuntimeProfile.PRODUCTION


def test_no_environment_variable_can_quietly_downgrade_the_profile() -> None:
    """A variable that turns PRODUCTION into FIXTURE turns a real machine into
    one that pretends."""
    import inspect

    source = inspect.getsource(profiles)
    assert "os.environ" not in source
    assert "getenv" not in source
