"""A step says what runs, and says it in one place.

Etap A of the `PlanStep.tool` → `PlanStep.target` migration (see
`docs/PLANSTEP_STEP_TARGET_DESIGN.md`): the new path exists, the old data still
loads, and nothing outside the engine can tell the difference. Every test here
guards one of the risks that document names — the ones that would otherwise be
found months later, in a report that quietly said the wrong thing.

Etap B added the checks that key on the target itself; Etap C removed the
transitional `.tool` accessors entirely, which is what the structural tests here
now guard.
"""

from __future__ import annotations

import sqlite3

from garis.agent.goal import Goal, Plan, PlanStep
from garis.agent.planner import Planner
from garis.agent.verify import StepEvidence
from garis.api import protocol as proto
from garis.config import ModelsConfig
from garis.kernel import CapabilityTarget, ToolTarget
from garis.models import ModelRouter
from garis.models.providers.fake import FakeProvider
from garis.runtime import Action
from garis.store import SCHEMA, Database
from garis.tasks import TaskStore
from garis.tasks.models import StepRecord, StepState

# ------------------------------------------------------------------ old plans


def test_a_plan_written_before_the_migration_still_loads() -> None:
    """A task parked overnight resumes into a build that speaks a new vocabulary."""
    plan = Plan.from_dict(
        {
            "summary": "stary plan",
            "steps": [{"key": "a", "tool": "look", "params": {"x": 1}, "purpose": "po co"}],
        }
    )

    assert [s.target for s in plan.steps] == [ToolTarget("look")]
    assert plan.steps[0].params == {"x": 1}
    assert plan.steps[0].purpose == "po co"


def test_a_plan_round_trips_through_the_journal_shape() -> None:
    for target in (ToolTarget("look"), CapabilityTarget("windows.process.list")):
        step = PlanStep(key="a", target=target, params={"n": 2}, expects="coś")
        assert PlanStep.from_dict(step.to_dict()).target == target


def test_a_step_naming_both_a_tool_and_a_capability_is_dropped_not_fatal() -> None:
    """One hallucinated line costs one step, not the whole task.

    The planner already drops steps it cannot run and explains why in `notes`;
    a raise here would sink a plan whose other steps were fine.
    """
    plan = Plan.from_dict(
        {
            "steps": [
                {"key": "a", "tool": "look", "capability": "windows.process.list"},
                {"key": "b", "tool": "note", "params": {"text": "x"}},
            ]
        }
    )

    assert [s.target for s in plan.steps] == [ToolTarget("note")]
    assert "krok 1" in plan.notes


def test_a_step_naming_nothing_is_dropped_with_a_reason() -> None:
    plan = Plan.from_dict({"steps": [{"key": "a", "params": {}}]})

    assert plan.steps == []
    assert plan.notes, "krok bez celu zniknął bez słowa wyjaśnienia"


# --------------------------------------------- the accessor that no longer is


def test_a_step_has_no_tool_attribute_at_all() -> None:
    """Etap C: the transitional accessor is gone, not merely discouraged.

    It existed to be loud — reading `.tool` on a capability raised rather than
    answering with its id, because `reflex.check` used to look names up in a
    dict and return "I cannot check this" for a miss, silently turning a
    measured success into a reported failure. Everything keys on `target` now,
    so the accessor has no job left. Keeping it would only leave the mistake
    reachable.
    """
    step = PlanStep(key="a", target=CapabilityTarget("windows.process.list"))
    evidence = StepEvidence(target=ToolTarget("look"), purpose="", ok=True)

    assert not hasattr(step, "tool")
    assert not hasattr(evidence, "tool")
    assert step.name == "windows.process.list"
    assert evidence.name == "look"


def test_no_engine_code_reads_a_step_or_evidence_by_tool_name() -> None:
    """Structural, so the accessor cannot come back by the side door.

    A helper that reconstructs a name and dispatches on it would rebuild exactly
    the failure the target model removed, and would do it without touching any
    of the files this migration changed.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "core" / "src" / "garis"
    # Scoped to the packages that actually hold plan steps and evidence. `cli.py`
    # and `runtime/audit.py` also write `step.tool` and `e.tool`, but those names
    # are bound to `StepRecord` and to an audit entry — the two string surfaces
    # this migration deliberately keeps. Widening the scan would flag them and
    # teach the next reader to weaken the check.
    searched = [root / "agent", root / "tasks"]
    forbidden = re.compile(r"\b(step|item|evidence|e)\.tool\b")

    offenders = [
        f"{path.relative_to(root)}:{n}"
        for directory in searched
        for path in directory.rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if forbidden.search(line)
    ]

    assert offenders == [], f"ktoś znowu kluczuje po nazwie: {offenders}"


# ------------------------------------------------------------------ the record


def test_the_journal_row_keeps_naming_a_step_for_a_person() -> None:
    """`StepRecord.tool` is a label, not a key: it answers for both kinds.

    The task list reads it. Nothing in the engine dispatches on it, so the loud
    treatment that protects `reflex.check` would only break a screen here.
    """
    record = StepRecord(task_id="t", step_key="a", ordinal=0,
                        target=CapabilityTarget("windows.process.list"),
                        state=StepState.DONE)

    assert record.tool == "windows.process.list"
    view = proto.step_view(record)
    assert view["tool"] == "windows.process.list"
    assert view["capability"] == "windows.process.list"


def test_the_window_still_finds_the_key_it_has_always_read() -> None:
    """Contract with the frontend: `apps/desktop` reads `tool` off every step."""
    record = StepRecord(task_id="t", step_key="a", ordinal=0,
                        target=ToolTarget("disk_usage"), state=StepState.DONE)
    view = proto.step_view(record)

    assert view["tool"] == "disk_usage"
    assert view["capability"] == "", "narzędzie nie jest zdolnością"


# ---------------------------------------------------------------- persistence


def test_a_journal_row_written_before_the_migration_still_resumes(paths) -> None:
    """The row a reboot left behind is read by the build that comes after it."""
    # A v4 database: the schema as it stood before `task_steps.capability`.
    old = Database(paths.state_db, [(v, s) for v, s in SCHEMA if v <= 4])
    old.execute(
        "INSERT INTO task_steps(task_id, step_key, ordinal, tool, params, state,"
        " attempts, started_at) VALUES (?,?,?,?,?,?,1,?)",
        ("t1", "a", 0, "disk_usage", "{}", StepState.DONE.value, 0.0),
    )
    old.close()

    migrated = Database(paths.state_db, SCHEMA)
    try:
        step = TaskStore(migrated).steps_for("t1")[0]
        assert step.target == ToolTarget("disk_usage")
        assert step.state is StepState.DONE
    finally:
        migrated.close()


def test_the_migration_leaves_the_old_column_where_it_was(db) -> None:
    """`tool` is not dropped. A finished task's journal is a record."""
    columns = {row[1] for row in db.query("PRAGMA table_info(task_steps)")}
    assert {"tool", "capability"} <= columns


def test_a_capability_step_is_journalled_as_a_capability(db) -> None:
    store = TaskStore(db)
    store.step_started("t1", "a", 0, CapabilityTarget("windows.process.list"), {})

    row: sqlite3.Row = db.query("SELECT tool, capability FROM task_steps")[0]
    assert row["tool"] == ""
    assert row["capability"] == "windows.process.list"
    assert store.steps_for("t1")[0].target == CapabilityTarget("windows.process.list")


# ------------------------------------------------------------------- approvals


def test_an_approval_recorded_before_the_migration_still_matches_its_retry() -> None:
    """R2: `fingerprint()` is stored, so `Action.tool` may not change meaning.

    A user who answered "yes" last night must not be asked again this morning
    because the engine started describing the same operation differently.
    """
    action = Action(tool="delete_file", params={"path": "/x"}, target="local")
    again = Action(tool="delete_file", params={"path": "/x"}, target="local")

    assert action.fingerprint() == again.fingerprint()
    assert action.id != again.id, "odcisk nie może zależeć od identyfikatora wywołania"

    # And it is still built from the three things it was built from before, so a
    # hash written by the previous build still matches. Adding `Action.target`
    # for the step target — the change this migration deliberately did not make —
    # would have landed here.
    moved = Action(tool="windows.process.list", params={"path": "/x"}, target="local")
    assert moved.fingerprint() != action.fingerprint()


# -------------------------------------------------------------- the new path


async def test_the_loop_reaches_the_runner_through_the_target(runtime) -> None:
    """Etap A's whole point: a step's target is what the envelope receives."""
    seen: list[object] = []
    original = runtime.runner.run

    async def watching(target, action, context):
        seen.append(target)
        return await original(target, action, context)

    runtime.runner.run = watching  # type: ignore[method-assign]
    result = await runtime.perform_step(
        ToolTarget("note"), Action(tool="note", params={"text": "x"})
    )

    assert result.ok
    assert seen == [ToolTarget("note")]


# ------------------------------------------------- Etap B: the checks key on target


def test_a_reflex_answers_for_its_target_and_not_for_a_lookalike_name() -> None:
    """R1 closed. The reflex table is keyed on the target, not on its name.

    Before this, `check` took a string. A capability named after the tool it
    replaces would have been looked up, missed, and reported as "I cannot verify
    this" — turning a measured success into a failure. Keyed on the target, a
    capability is simply a different key, and repointing a reflex is a visible
    edit rather than a silent miss.
    """
    from garis.agent import reflex

    good = {"total": 100 * 1024**3, "free": 40 * 1024**3, "used": 60 * 1024**3,
            "percent": 60.0, "path": "C:"}

    assert reflex.check(ToolTarget("disk_usage"), good) == ""
    assert reflex.answer(ToolTarget("disk_usage"), good)

    # Same trailing name, different kind of thing. It is not this reflex.
    assert reflex.check(CapabilityTarget("disk_usage"), good) != ""
    assert reflex.answer(CapabilityTarget("disk_usage"), good) == ""


def test_the_planner_drops_a_step_it_cannot_run_and_says_why(runtime) -> None:
    """The reason reaches `notes`, so a dropped step is never silent.

    The planner asks the resolver, not the tool registry: a step naming a
    capability has to come back "unknown capability", never "unknown tool".
    """
    router = ModelRouter([FakeProvider("{}", stub=True)], ModelsConfig())
    planner = Planner(router, runtime.registry, language="pl")

    plan = planner._sanitise(
        Plan(steps=[
            PlanStep(key="a", target=CapabilityTarget("nie.ma.takiej")),
            PlanStep(key="b", target=ToolTarget("note"), params={"text": "x"}),
        ]),
        Goal("cel"),
    )

    assert [s.target for s in plan.steps] == [ToolTarget("note")]
    assert "nieznana zdolność" in plan.notes
