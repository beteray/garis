"""A step says what runs, and says it in one place.

Etap A of the `PlanStep.tool` → `PlanStep.target` migration (see
`docs/PLANSTEP_STEP_TARGET_DESIGN.md`): the new path exists, the old data still
loads, and nothing outside the engine can tell the difference. Every test here
guards one of the risks that document names — the ones that would otherwise be
found months later, in a report that quietly said the wrong thing.
"""

from __future__ import annotations

import sqlite3

import pytest

from garis.agent.goal import Plan, PlanStep
from garis.agent.verify import StepEvidence
from garis.api import protocol as proto
from garis.kernel import CapabilityTarget, ToolTarget
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


# ------------------------------------------------- the loud compatibility edge


def test_reading_tool_off_a_capability_step_raises_rather_than_answering() -> None:
    """The single most expensive silent failure this migration could produce.

    `reflex.check` looks a tool name up in a plain dict and returns "I cannot
    check this" for anything it does not recognise — it does not raise. So a
    `.tool` that helpfully answered `windows.process.list` would turn a verified
    success into a reported failure, with green tests and a clean type check.
    """
    step = PlanStep(key="a", target=CapabilityTarget("windows.process.list"))

    with pytest.raises(TypeError):
        _ = step.tool
    assert step.name == "windows.process.list"


def test_reading_tool_off_capability_evidence_raises_too() -> None:
    evidence = StepEvidence(target=CapabilityTarget("windows.process.list"),
                            purpose="odczyt", ok=True)

    with pytest.raises(TypeError):
        _ = evidence.tool
    assert evidence.name == "windows.process.list"


def test_a_tool_step_still_answers_tool() -> None:
    """The edge is loud only where it must be: legacy steps are untouched."""
    assert PlanStep(key="a", target=ToolTarget("look")).tool == "look"
    assert StepEvidence(target=ToolTarget("look"), purpose="", ok=True).tool == "look"


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
