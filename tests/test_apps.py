"""Uruchomiony znaczy „widziałem go na liście", a nie „wywołanie wróciło".

The legacy `app_launch` tool answers `{"pid": 8112}` the moment `CreateProcess`
succeeds. That is a fact about a system call and it is routinely false about the
program: a launcher stub exits immediately, a missing DLL kills the process a
second later, an installer redirects to an existing instance.

So the capability reads the process table and the verifier is handed that
reading. These tests hold the four places that claim could sneak back in: the
launch itself, the check, the effect ledger and recovery after a crash.

The process table is faked at `_processes`, the module's one seam — the same
place `windows.process.list` reads. Everything above it is production code.
"""

from __future__ import annotations

import pytest

from garis.capabilities import apps
from garis.capabilities import processes as processes_module
from garis.capabilities.base import Invocation, Outcome
from garis.capabilities.native import NativeCapabilityExecutor
from garis.capabilities.processes import LIST as PROCESS_LIST
from garis.capabilities.registry import REGISTRY, CapabilityRegistry
from garis.errors import ExecutionError
from garis.kernel.contracts import (
    CapabilityTarget,
    Effect,
    EffectDisposition,
    ExecutionContext,
    RuntimeProfile,
)
from garis.kernel.effects import EffectStore
from garis.kernel.outbox import EventOutbox
from garis.kernel.recovery import RecoveryStatus
from garis.runtime import TargetResolver, ToolRegistry
from garis.runtime.action import Action

LAUNCH = "windows.app.launch"


def row(name: str, pid: int = 1000):
    return {"name": name, "pid": pid, "user": "ja", "memory_mb": 1.0, "cpu_percent": 0.0}


#: A plausible machine. Never empty: an empty table is a failed reading, and the
#: tests that mean "nothing is running" say so by leaving the program out of it.
IDLE = [row("systemd", 1), row("explorer.exe", 2), row("python", 3)]


@pytest.fixture
def machine(monkeypatch):
    """The process table, plus what a launch does to it.

    `appears` is the honest variable: a launcher that starts nothing looks
    exactly like one that starts something slow, and the difference is only
    visible in the table.
    """
    state = {"rows": list(IDLE), "spawned": [], "appears": "discord.exe", "boom": None}

    def processes():
        return list(state["rows"])

    async def spawn(target, arguments):
        if state["boom"] is not None:
            raise state["boom"]
        state["spawned"].append((target, list(arguments)))
        if state["appears"]:
            state["rows"] = [*state["rows"], row(state["appears"], 8112)]
        return 8112

    monkeypatch.setattr(apps, "_processes", processes)
    # The same table the launch reads is the one recovery inspects with, so the
    # capability that recovery actually runs has to see it too. Two different
    # fake machines would make the recovery tests prove nothing.
    monkeypatch.setattr(processes_module, "_processes", processes)
    monkeypatch.setattr(apps, "_spawn", spawn)
    # The waiting loop is real; the clock it waits against is not, because a
    # test that takes twelve seconds to prove a negative is a test nobody runs.
    monkeypatch.setattr(apps, "APPEAR_TIMEOUT", 0.05)
    monkeypatch.setattr(apps, "APPEAR_INTERVAL", 0.01)
    return state


async def launch(args: dict) -> Outcome:
    return await apps.LAUNCH.executor(Invocation(capability=LAUNCH, args=args))


async def judged(args: dict):
    outcome = await launch(args)
    verdict = await apps.verify_launch(Invocation(LAUNCH, args), outcome)
    return outcome, verdict


# ------------------------------------------------------------------- the name


@pytest.mark.parametrize(
    "target, expected",
    [
        ("discord", "discord"),
        ("Discord.exe", "discord"),
        (r"C:\Program Files\Discord\Discord.exe", "discord"),
        ("/usr/bin/firefox", "firefox"),
        ("Notatnik.lnk", "notatnik"),
    ],
)
def test_the_process_to_look_for_is_derived_from_what_was_asked(target, expected) -> None:
    assert apps.process_name_for(target) == expected


def test_a_stated_process_name_wins_over_the_guess(machine) -> None:
    """`msedge` starts `msedge.exe`; `steam://run/730` starts something else
    entirely, and only the caller knows what."""
    assert apps.LAUNCH.goal_of({"target": "steam://run/730",
                                "process_name": "cs2"})["process_name"] == "cs2"


# ---------------------------------------------------------------- the launch


async def test_a_program_that_appears_on_the_list_counts_as_running(machine) -> None:
    outcome, verdict = await judged({"target": "discord"})

    assert outcome.ok and verdict.ok and verdict.checked
    assert outcome.value["running"] is True
    assert outcome.value["pids"] == [8112]
    assert outcome.disposition is EffectDisposition.APPLIED


async def test_a_launcher_that_returns_a_pid_and_starts_nothing_is_not_success(
    machine,
) -> None:
    """The exact claim this capability exists to refuse."""
    machine["appears"] = ""          # spawn succeeds, nothing joins the table

    outcome, verdict = await judged({"target": "discord"})

    assert machine["spawned"], "proces został wystartowany"
    assert outcome.value["spawned_pid"] == 8112
    assert outcome.value["running"] is False
    assert not verdict.ok and verdict.checked
    assert "nie widzę" in verdict.note


async def test_something_already_running_is_never_started_a_second_time(
    machine,
) -> None:
    """Also what stops a resumed task opening a second window."""
    machine["rows"] = [*IDLE, row("discord.exe", 700)]

    outcome, verdict = await judged({"target": "discord"})

    assert machine["spawned"] == [], "nic nie powinno zostać uruchomione"
    assert outcome.value["already_running"] is True
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert verdict.ok and verdict.checked and "już działał" in verdict.note


async def test_a_binary_that_does_not_exist_changed_nothing(machine) -> None:
    # What the real `_spawn` raises for a missing binary: the one launch failure
    # that is safe to retry, and safe precisely because nothing happened.
    machine["boom"] = ExecutionError("Nie udało się uruchomić: brak pliku")

    outcome, verdict = await judged({"target": "nie-ma-takiego"})

    assert not outcome.ok
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert outcome.disposition.permits_retry, "skoro nic się nie stało, wolno spróbować"
    assert not verdict.ok and verdict.checked


async def test_an_unreadable_process_table_stops_the_launch_before_it_starts(
    machine,
) -> None:
    """A broken check must not become a reason to start a second copy."""
    machine["rows"] = []

    outcome, verdict = await judged({"target": "discord"})

    assert machine["spawned"] == []
    assert not outcome.ok
    assert outcome.disposition is EffectDisposition.NOT_APPLIED
    assert not verdict.ok


async def test_arguments_reach_the_program(machine) -> None:
    await launch({"target": "discord", "arguments": ["--start-minimized"]})

    assert machine["spawned"] == [("discord", ["--start-minimized"])]


# ---------------------------------------------------------------- the verdict


async def test_a_reading_that_never_happened_is_unchecked_not_negative(
    machine,
) -> None:
    """Saying the program is not there is a claim about the machine. A failed
    reading is a claim about the reading, and the two are not the same answer."""
    outcome = await launch({"target": "discord"})
    blind = Outcome(ok=True, value={**outcome.value, "scanned": 0},
                    evidence=outcome.evidence)

    verdict = await apps.verify_launch(Invocation(LAUNCH, {}), blind)

    assert not verdict.checked, "nieudany odczyt nie jest sprawdzeniem"
    assert not verdict.ok


async def test_evidence_that_disagrees_with_the_result_is_refused(machine) -> None:
    outcome = await launch({"target": "discord"})
    tampered = Outcome(ok=True, value=outcome.value,
                       evidence={**outcome.evidence, "running": False})

    verdict = await apps.verify_launch(Invocation(LAUNCH, {}), tampered)

    assert not verdict.ok and verdict.checked


async def test_a_result_that_only_echoes_the_request_is_refused() -> None:
    echo = {"target": "discord", "process_name": "discord"}
    verdict = await apps.verify_launch(
        Invocation(LAUNCH, {}), Outcome(ok=True, value=echo, evidence=echo)
    )

    assert not verdict.ok


# -------------------------------------------------------------- what is said


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"target": "Discord", "running": True, "already_running": False,
          "scanned": 300}, "Discord działa."),
        ({"target": "Discord", "running": True, "already_running": True,
          "scanned": 300}, "Discord już działał."),
        ({"target": "Discord", "running": False, "already_running": False,
          "scanned": 300},
         "Uruchomiłem Discord, ale nie widzę go na liście procesów."),
    ],
)
def test_the_sentence_reports_the_reading_and_not_the_launch(value, expected) -> None:
    from garis.agent import reflex

    assert reflex.answer(CapabilityTarget(LAUNCH), value) == expected


def test_a_launch_with_no_reading_behind_it_says_nothing_at_all() -> None:
    """`scanned: 0` is a failed check. A sentence built on it would be the
    invention the whole presenter table exists to prevent."""
    from garis.agent import reflex

    assert reflex.answer(
        CapabilityTarget(LAUNCH),
        {"target": "Discord", "running": True, "already_running": False, "scanned": 0},
    ) == ""


# --------------------------------------------------------------- the envelope


@pytest.fixture
def catalogue(capability_runner) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.add(REGISTRY.get(LAUNCH))
    registry.add(REGISTRY.get(PROCESS_LIST.id))
    capability_runner.register_executor("capability", NativeCapabilityExecutor(registry))
    registry.bind(capability_runner)
    return registry


async def run_launch(runner, args, *, step_key="open"):
    return await runner.run(
        CapabilityTarget(LAUNCH),
        Action(tool=LAUNCH, params=args, task_id="t1", step_key=step_key),
        ExecutionContext(task_id="t1", step_key=step_key,
                         runtime_profile=RuntimeProfile.TEST),
    )


async def test_the_launch_declares_an_effect_and_records_its_goal(
    capability_runner, catalogue, machine, db
) -> None:
    result = await run_launch(capability_runner, {"target": "discord"})

    assert result.ok and result.verified
    record = EffectStore(db).load(result.effect_id)
    assert record is not None
    assert record.goal == {"process_name": "discord", "target": "discord"}
    assert record.disposition is EffectDisposition.APPLIED
    assert Effect.EXEC in REGISTRY.get(LAUNCH).effects


async def test_a_resumed_task_replays_the_launch_instead_of_repeating_it(
    capability_runner, catalogue, machine
) -> None:
    """The scenario the effect ledger exists for: Discord opens once."""
    first = await run_launch(capability_runner, {"target": "discord"})
    machine["spawned"].clear()

    second = await run_launch(capability_runner, {"target": "discord"})

    assert first.effect_id == second.effect_id
    assert second.replayed
    assert machine["spawned"] == [], "drugie okno Discorda to ta awaria"


# ------------------------------------------------------------------ recovery


@pytest.fixture
def recovery(capability_runner, catalogue, db, bus):
    from garis.kernel.recovery import RecoveryStore
    from garis.runtime import AuditLog
    from garis.runtime.recovery import EffectRecoveryService

    return EffectRecoveryService(
        effects=EffectStore(db),
        recoveries=RecoveryStore(db),
        runner=capability_runner,
        resolver=TargetResolver(ToolRegistry(), catalogue),
        capabilities=catalogue,
        audit=AuditLog(db),
        outbox=EventOutbox(db, bus),
    )


def crashed(db, goal: dict, effect_id: str = "eff-launch"):
    effects = EffectStore(db)
    effects.reserve(effect_id, capability_id=LAUNCH, task_id="t1", step_key="open",
                    args={}, goal=goal)
    effects.sweep_unsettled()
    return effects.load(effect_id)


async def test_after_a_crash_the_list_is_read_and_nothing_is_launched(
    recovery, db, machine
) -> None:
    crashed(db, {"process_name": "discord", "target": "discord"})
    machine["rows"] = [*IDLE, row("discord.exe", 700)]

    outcome = await recovery.reconcile("eff-launch")

    assert machine["spawned"] == [], "recovery ogląda, nigdy nie powtarza"
    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert outcome.verification.goal_met and outcome.verification.checked


async def test_a_running_program_is_never_proof_that_garis_started_it(
    recovery, db, machine
) -> None:
    """Someone can click the icon while the engine is down."""
    crashed(db, {"process_name": "discord", "target": "discord"})
    machine["rows"] = [*IDLE, row("discord.exe", 700)]

    outcome = await recovery.reconcile("eff-launch")

    assert outcome.disposition is EffectDisposition.UNKNOWN
    assert not outcome.disposition.permits_retry


async def test_a_program_that_is_not_there_is_reported_as_not_there(
    recovery, db, machine
) -> None:
    crashed(db, {"process_name": "discord", "target": "discord"})

    outcome = await recovery.reconcile("eff-launch")

    assert outcome.status is RecoveryStatus.RESOLVED_GOAL_ONLY
    assert not outcome.verification.goal_met and outcome.verification.checked
    assert outcome.disposition is EffectDisposition.UNKNOWN, (
        "brak procesu nie dowodzi, ze uruchomienie nie doszlo do skutku: "
        "program mogl wystartowac i zaraz sie wywrocic"
    )


async def test_recovery_says_so_when_nobody_wrote_down_what_to_look_for(
    recovery, db, machine
) -> None:
    effects = EffectStore(db)
    effects.reserve("eff-launch", capability_id=LAUNCH, task_id="t1",
                    step_key="open", args={})
    effects.sweep_unsettled()

    outcome = await recovery.reconcile("eff-launch")

    assert outcome.status is RecoveryStatus.STILL_UNKNOWN
    assert not outcome.verification.goal_met
