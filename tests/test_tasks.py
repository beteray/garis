"""Durability: a task outlives the process that started it.

The scenario from the spec — a job runs for hours, hits a payment at night, waits,
the machine reboots, the user approves from a phone in the morning, the job
finishes without redoing its work — is exercised here end to end.
"""

from __future__ import annotations

import asyncio

from garis.agent import Planner, Verifier
from garis.config import Config, ModelsConfig, TasksConfig
from garis.models import ModelRouter
from garis.models.providers.fake import FakeProvider, plan_reply, role_aware
from garis.runtime import ApprovalBroker, Effect, ParamSpec, Runtime
from garis.store import Database
from garis.tasks import TaskState, TaskStore, TaskSupervisor


def build_supervisor(
    runtime: Runtime,
    bus,
    config: Config,
    db: Database,
    plans: str | list[str],
) -> tuple[TaskSupervisor, TaskStore, FakeProvider]:
    # Stands in for a real provider — see the note in tests/test_agent.py.
    provider = FakeProvider(role_aware(plans), stub=False)
    router = ModelRouter([provider], ModelsConfig(), bus=bus)
    planner = Planner(router, runtime.registry, memory=runtime.memory, language="pl")
    store = TaskStore(db)
    supervisor = TaskSupervisor(
        store, runtime, planner, Verifier(router, language="pl"), bus=bus, config=config
    )
    return supervisor, store, provider


# --------------------------------------------------------------------- basics


async def test_task_reaches_finished(runtime, bus, config, db) -> None:
    supervisor, store, _ = build_supervisor(
        runtime, bus, config, db, plan_reply([{"key": "a", "tool": "look", "params": {}}]),
    )
    task = supervisor.submit("sprawdź stan")
    done = await supervisor.wait(task.id, timeout=10)

    assert done.state is TaskState.FINISHED
    # The step ran and returned; nothing declared a criterion, so nothing checked
    # the outcome. Finished, honestly unverified.
    assert done.report and done.report["verified"] is False
    assert store.steps_for(task.id)[0].state.value == "done"


async def test_stopping_a_task(runtime, bus, config, db) -> None:
    supervisor, store, _ = build_supervisor(
        runtime, bus, config, db, plan_reply([{"key": "a", "tool": "slow", "params": {}}]),
    )
    task = supervisor.submit("długie zadanie")
    await asyncio.sleep(0.02)
    assert supervisor.stop(task.id)
    await asyncio.sleep(0.05)
    assert store.require(task.id).state is TaskState.STOPPED


async def test_tasks_run_in_parallel_up_to_the_limit(runtime, bus, db) -> None:
    config = Config(tasks=TasksConfig(max_parallel=2))
    active = 0
    peak = 0

    @runtime.registry.tool("wait_a_bit", "Czeka.", params={}, effects=[Effect.READ])
    async def wait_a_bit(ctx):  # type: ignore[no-untyped-def]
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "ok"

    supervisor, _, _ = build_supervisor(
        runtime, bus, config, db, plan_reply([{"key": "a", "tool": "wait_a_bit", "params": {}}]),
    )
    tasks = [supervisor.submit(f"zadanie {i}") for i in range(4)]
    for task in tasks:
        await supervisor.wait(task.id, timeout=10)

    assert peak == 2, f"limit równoległości nie działa (szczyt {peak})"


# ------------------------------------------------------------------- blocking


async def test_payment_blocks_the_task_and_persists_the_question(
    runtime, bus, config, db
) -> None:
    supervisor, _, _ = build_supervisor(
        runtime, bus, config, db,
        plan_reply([{"key": "zaplac", "tool": "pay", "params": {"amount": 59.0}}]),
    )
    task = supervisor.submit("opłać domenę")
    blocked = await supervisor.wait(task.id, timeout=10)

    assert blocked.state is TaskState.BLOCKED
    pending = ApprovalBroker(db, bus).pending(task_id=task.id)
    assert len(pending) == 1
    assert "płatnoś" in pending[0].prompt.lower()


async def test_approval_after_restart_finishes_the_task(runtime, bus, config, db) -> None:
    """The full overnight story: block, restart, approve elsewhere, resume."""
    plan = plan_reply([{"key": "zaplac", "tool": "pay", "params": {"amount": 59.0}}])

    first, _, _ = build_supervisor(runtime, bus, config, db, plan)
    task = first.submit("opłać domenę")
    assert (await first.wait(task.id, timeout=10)).state is TaskState.BLOCKED

    # --- process dies here; only the database survives ---
    approvals = ApprovalBroker(db, bus)
    request = approvals.pending(task_id=task.id)[0]
    approvals.resolve(request.id, True, by="mobile")

    second, store2, _ = build_supervisor(runtime, bus, config, db, plan)
    reloaded = store2.require(task.id)
    assert reloaded.state is TaskState.BLOCKED, "stan przetrwał restart"

    second.resume(task.id)
    finished = await second.wait(task.id, timeout=10)
    assert finished.state is TaskState.FINISHED


async def test_resuming_does_not_repeat_completed_work(runtime, bus, config, db) -> None:
    """Idempotence: the journal is consulted before anything is executed again."""
    calls: list[str] = []

    @runtime.registry.tool(
        "charge_once",
        "Operacja, która nie może wykonać się dwa razy.",
        params={"what": ParamSpec("string", required=True)},
        effects=[Effect.WRITE],
    )
    async def charge_once(ctx, what):  # type: ignore[no-untyped-def]
        calls.append(what)
        return {"done": what}

    plan = plan_reply(
        [
            {"key": "raz", "tool": "charge_once", "params": {"what": "faktura"}},
            {"key": "dwa", "tool": "pay", "params": {"amount": 10.0}},
        ]
    )

    first, _, _ = build_supervisor(runtime, bus, config, db, plan)
    task = first.submit("rozlicz fakturę i zapłać")
    assert (await first.wait(task.id, timeout=10)).state is TaskState.BLOCKED
    assert calls == ["faktura"]

    approvals = ApprovalBroker(db, bus)
    approvals.resolve(approvals.pending(task_id=task.id)[0].id, True)

    second, _, _ = build_supervisor(runtime, bus, config, db, plan)
    second.resume(task.id)
    finished = await second.wait(task.id, timeout=10)

    assert finished.state is TaskState.FINISHED
    assert calls == ["faktura"], "krok wykonany przed restartem został powtórzony"


# -------------------------------------------------------------------- recovery


async def test_crash_recovery_relaunches_interrupted_tasks(runtime, bus, config, db) -> None:
    """Anything left RUNNING did not choose to stop, so it is picked back up."""
    plan = plan_reply([{"key": "a", "tool": "look", "params": {}}])
    supervisor, store, _ = build_supervisor(runtime, bus, config, db, plan)

    task = supervisor.submit("zadanie przerwane awarią")
    await supervisor.wait(task.id, timeout=10)
    # Pretend the process was killed mid-flight.
    store.set_state(task.id, TaskState.RUNNING)

    fresh, _, _ = build_supervisor(runtime, bus, config, db, plan)
    recovered = await fresh.recover()

    assert [t.id for t in recovered] == [task.id]
    assert (await fresh.wait(task.id, timeout=10)).state is TaskState.FINISHED


async def test_recovery_leaves_blocked_tasks_alone(runtime, bus, config, db) -> None:
    """A blocked task is correctly waiting; restarting it would re-ask the question."""
    plan = plan_reply([{"key": "a", "tool": "pay", "params": {"amount": 5.0}}])
    supervisor, store, _ = build_supervisor(runtime, bus, config, db, plan)
    task = supervisor.submit("zapłać")
    await supervisor.wait(task.id, timeout=10)

    fresh, _, provider = build_supervisor(runtime, bus, config, db, plan)
    recovered = await fresh.recover()

    assert recovered == []
    assert store.require(task.id).state is TaskState.BLOCKED
    assert provider.calls == [], "zablokowane zadanie nie powinno wołać modelu ponownie"


async def test_running_steps_are_reset_for_retry(runtime, bus, config, db) -> None:
    plan = plan_reply([{"key": "a", "tool": "look", "params": {}}])
    supervisor, store, _ = build_supervisor(runtime, bus, config, db, plan)
    task = supervisor.submit("zadanie")
    await supervisor.wait(task.id, timeout=10)

    store.execute = store.db.execute  # readability below
    store.db.execute(
        "UPDATE task_steps SET state = 'running' WHERE task_id = ?", (task.id,)
    )
    store.set_state(task.id, TaskState.RUNNING)

    fresh, store2, _ = build_supervisor(runtime, bus, config, db, plan)
    await fresh.recover()
    await fresh.wait(task.id, timeout=10)
    assert store2.require(task.id).state is TaskState.FINISHED


# ------------------------------------------------------------------ inspection


async def test_journal_is_readable_for_progress_questions(runtime, bus, config, db) -> None:
    """Backs "co teraz robisz?" and "co zrobiłeś przez ostatnią godzinę?"."""
    supervisor, store, _ = build_supervisor(
        runtime, bus, config, db,
        plan_reply([
            {"key": "a", "tool": "look", "params": {}, "purpose": "rozpoznanie"},
            {"key": "b", "tool": "note", "params": {"text": "x"}, "purpose": "zapis"},
        ]),
    )
    task = supervisor.submit("zrób dwie rzeczy")
    await supervisor.wait(task.id, timeout=10)

    steps = store.steps_for(task.id)
    assert [s.tool for s in steps] == ["look", "note"]
    assert all(s.state.value == "done" for s in steps)

    activity = runtime.audit.activity_since(0)
    assert activity["actions"] >= 2
    assert task.id in activity["tasks"]


async def test_task_listing_filters_by_state(runtime, bus, config, db) -> None:
    supervisor, store, _ = build_supervisor(
        runtime, bus, config, db, plan_reply([{"key": "a", "tool": "look", "params": {}}]),
    )
    task = supervisor.submit("cel")
    await supervisor.wait(task.id, timeout=10)

    assert [t.id for t in store.list(state=TaskState.FINISHED)] == [task.id]
    assert store.list(state=TaskState.PENDING) == []


async def test_events_describe_the_whole_lifecycle(runtime, bus, config, db) -> None:
    supervisor, _, _ = build_supervisor(
        runtime, bus, config, db, plan_reply([{"key": "a", "tool": "look", "params": {}}]),
    )
    task = supervisor.submit("cel")
    await supervisor.wait(task.id, timeout=10)

    topics = [e.topic for e in bus.recent("task.*")]
    assert "task.created" in topics
    assert "task.started" in topics
    assert "task.finished" in topics


async def test_whole_app_accepts_a_goal(garis) -> None:
    """The public entry point: build(), then hand it an outcome."""
    task = await garis.do("sprawdź, ile miejsca zostało na dysku", timeout=20)
    assert task.state in (TaskState.FINISHED, TaskState.FAILED, TaskState.BLOCKED)
    assert garis.registry.has("disk_usage")
    assert task.report is not None


async def test_blocked_task_remembers_what_it_is_waiting_for(runtime, bus, config, db) -> None:
    """The question must survive in the database, not only in the live outcome.

    Otherwise the tray, the CLI and the phone all show a blocked task with no way
    to tell the user why it stopped.
    """
    supervisor, _, _ = build_supervisor(
        runtime, bus, config, db,
        plan_reply([{"key": "a", "tool": "pay", "params": {"amount": 12.0}}]),
    )
    task = supervisor.submit("opłać coś")
    await supervisor.wait(task.id, timeout=10)

    reloaded = TaskStore(db).require(task.id)
    assert reloaded.state is TaskState.BLOCKED
    assert reloaded.report is not None
    assert "płatnoś" in reloaded.report["short"].lower()
    assert "płatnoś" in reloaded.error.lower()
