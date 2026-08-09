"""An effect happens once, and an event exists only because something committed.

Both of these used to be promises made by ordering: perform, then remember;
save, then emit. A crash in the middle of either breaks the promise silently,
and the failure mode is the worst kind — a program launched twice, or a task
that finished with nobody told.
"""

from __future__ import annotations

import time

import pytest

from garis.errors import StoreError
from garis.events import EventBus
from garis.kernel import CapabilityError, EvidenceRecord, Failure
from garis.kernel.effects import (
    EffectDisposition,
    EffectState,
    EffectStore,
    arguments_hash,
)
from garis.kernel.outbox import EventOutbox, deduplicate


@pytest.fixture
def effects(db) -> EffectStore:
    return EffectStore(db)


@pytest.fixture
def outbox(db) -> EventOutbox:
    return EventOutbox(db, EventBus())


# ------------------------------------------------------------------- effects


def test_the_first_caller_wins_and_the_second_is_told_who(effects: EffectStore) -> None:
    first = effects.reserve("e1", capability_id="windows.audio.set", args={"percent": 30})
    second = effects.reserve("e1", capability_id="windows.audio.set", args={"percent": 30})

    assert first.granted
    assert not second.granted
    assert second.existing is not None
    assert second.existing.state is EffectState.RESERVED


def test_a_finished_effect_is_never_repeatable(effects: EffectStore) -> None:
    effects.reserve("e2", capability_id="windows.app.launch", args={"app": "discord"})
    effects.complete("e2", ok=True, outcome={"pid": 4120},
                     evidence=[EvidenceRecord.new("windows.app.launch", 1, {"pid": 4120})])

    record = effects.load("e2")
    assert record is not None
    assert record.state is EffectState.DONE
    assert not record.repeatable
    assert record.outcome == {"pid": 4120}
    assert record.evidence[0]["fields"] == {"pid": 4120}


def test_a_failure_that_proved_nothing_changed_may_be_tried_again(
    effects: EffectStore,
) -> None:
    effects.reserve("e3", capability_id="windows.audio.set", args={"percent": 30})
    effects.complete("e3", ok=False, reason="brak urządzenia",
                     disposition=EffectDisposition.NOT_APPLIED)

    record = effects.load("e3")
    assert record is not None and record.state is EffectState.FAILED
    assert record.repeatable, "dowiedziona porażka bez skutku — wolno spróbować jeszcze raz"


def test_a_failure_that_may_have_changed_something_is_not_tried_again(
    effects: EffectStore,
) -> None:
    """The dangerous half of "it failed".

    An executor that sent the message and then died on the readback has failed
    *and* applied. Retrying that is how one message becomes two, so a failure
    that says nothing about its effect is not repeatable.
    """
    effects.reserve("e3b", capability_id="chat.send", args={"to": "ala"})
    effects.complete("e3b", ok=False, reason="zerwane połączenie")

    record = effects.load("e3b")
    assert record is not None and record.state is EffectState.FAILED
    assert record.disposition is EffectDisposition.UNKNOWN
    assert not record.repeatable, "porażka nie dowodzi, że nic się nie stało"


def test_the_same_id_for_different_arguments_is_refused(effects: EffectStore) -> None:
    """A reused effect id would return someone else's result, or act under a
    used identity. Neither is acceptable, so it raises."""
    effects.reserve("e4", capability_id="windows.audio.set", args={"percent": 30})

    with pytest.raises(CapabilityError) as raised:
        effects.reserve("e4", capability_id="windows.audio.set", args={"percent": 80})
    assert raised.value.failure is Failure.INVALID_INPUT


def test_a_reservation_that_never_settled_becomes_uncertain(effects: EffectStore) -> None:
    """The footprint of a process that died mid-action."""
    effects.reserve("e5", capability_id="windows.app.launch", args={"app": "discord"})

    stranded = effects.sweep_unsettled()

    assert [record.effect_id for record in stranded] == ["e5"]
    after = effects.load("e5")
    assert after is not None
    assert after.state is EffectState.UNCERTAIN
    assert not after.repeatable, "nie wolno powtarzać czegoś, co mogło się wydarzyć"
    assert "nie wiem" in after.reason.lower()


def test_a_settled_effect_survives_the_sweep(effects: EffectStore) -> None:
    effects.reserve("e6", capability_id="x", args={})
    effects.complete("e6", ok=True)
    assert effects.sweep_unsettled() == []
    record = effects.load("e6")
    assert record is not None and record.state is EffectState.DONE


def test_arguments_hash_is_stable_and_order_independent() -> None:
    assert arguments_hash({"a": 1, "b": 2}) == arguments_hash({"b": 2, "a": 1})
    assert arguments_hash({"a": 1}) != arguments_hash({"a": 2})
    # Anything unserialisable still hashes rather than exploding: a coarse hash
    # beats a crash inside the thing that prevents duplicate payments.
    assert arguments_hash({"fn": object()})


def test_effects_are_listed_per_task(effects: EffectStore) -> None:
    effects.reserve("t-1", capability_id="a", task_id="task", args={})
    effects.reserve("t-2", capability_id="b", task_id="task", args={})
    effects.reserve("other", capability_id="c", task_id="inny", args={})
    assert [r.effect_id for r in effects.for_task("task")] == ["t-1", "t-2"]


# ---------------------------------------------------------------------- goals


def test_a_crashed_effect_still_says_what_it_was_trying_to_do(
    effects: EffectStore,
) -> None:
    """The whole reason the goal is written at reservation and not at the end.

    A crash settles nothing, so anything recorded on completion is missing from
    exactly the rows recovery has to read.
    """
    effects.reserve("g-1", capability_id="windows.audio.master.set",
                    args={"percent": 30}, goal={"volume_percent": 30})
    effects.sweep_unsettled()

    record = effects.load("g-1")
    assert record is not None and record.state is EffectState.UNCERTAIN
    assert record.goal == {"volume_percent": 30}


def test_the_arguments_hash_still_cannot_answer_what_was_wanted(
    effects: EffectStore,
) -> None:
    """Two facts, two fields. The hash proves sameness; the goal states intent."""
    effects.reserve("g-2", capability_id="c", args={"percent": 30},
                    goal={"volume_percent": 30})

    record = effects.load("g-2")
    assert record is not None
    assert record.arguments_hash and record.arguments_hash != str(record.goal)


def test_a_goal_is_redacted_like_anything_else_that_gets_stored(
    effects: EffectStore,
) -> None:
    """It is written down and later shown, so it goes through the same filter as
    every other stored field."""
    effects.reserve("g-3", capability_id="c", args={},
                    goal={"path": "raport.txt", "token": "sk-prawdziwy"})

    record = effects.load("g-3")
    assert record is not None and record.goal is not None
    assert record.goal["path"] == "raport.txt"
    assert record.goal["token"] == "[ukryte]"


def test_a_capability_that_states_no_goal_stores_none(effects: EffectStore) -> None:
    """Silence is a legitimate answer and must not become an empty promise."""
    effects.reserve("g-4", capability_id="c", args={"a": 1})
    assert (record := effects.load("g-4")) is not None and record.goal is None


def test_a_retry_keeps_the_goal_of_the_attempt_that_failed(
    effects: EffectStore,
) -> None:
    """A retry under the same effect id is the same intent, so the goal survives
    even when the retrying caller does not restate it."""
    effects.reserve("g-5", capability_id="c", args={"percent": 30},
                    goal={"volume_percent": 30})
    effects.complete("g-5", ok=False, reason="brak urządzenia",
                     disposition=EffectDisposition.NOT_APPLIED)

    again = effects.reserve("g-5", capability_id="c", args={"percent": 30})

    assert again.granted
    assert again.record is not None and again.record.goal == {"volume_percent": 30}
    assert (stored := effects.load("g-5")) is not None
    assert stored.goal == {"volume_percent": 30}


# -------------------------------------------------------------------- outbox


def test_an_event_and_its_fact_land_together(db, outbox: EventOutbox) -> None:
    """The whole point: one transaction, or neither."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO tasks(id, goal, state, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("task-1", "cel", "running", time.time(), time.time()),
        )
        outbox.stage(conn, "task.started", task_id="task-1", payload={"goal": "cel"})

    assert db.one("SELECT id FROM tasks WHERE id = 'task-1'") is not None
    assert [event.topic for event in outbox.pending()] == ["task.started"]


def test_a_rolled_back_fact_takes_its_event_with_it(db, outbox: EventOutbox) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO tasks(id, goal, state, created_at, updated_at) VALUES (?,?,?,?,?)",
            ("task-2", "cel", "running", time.time(), time.time()),
        )
        outbox.stage(conn, "task.started", task_id="task-2")
        raise RuntimeError("coś padło w połowie")

    assert db.one("SELECT id FROM tasks WHERE id = 'task-2'") is None
    assert outbox.pending() == [], "zdarzenie o czymś, co się nie stało"


def test_publication_happens_only_after_the_commit(db, outbox: EventOutbox) -> None:
    before = len(outbox.bus.recent("*", limit=500))

    outbox.record("task.finished", task_id="task-3", payload={"report": "gotowe"})
    assert len(outbox.bus.recent("*", limit=500)) == before, "nic przed drenażem"

    published = outbox.drain()
    assert [event.topic for event in published] == ["task.finished"]
    assert [e.topic for e in outbox.bus.recent("*", limit=500)[before:]] == ["task.finished"]
    assert outbox.pending() == []


def test_sequence_orders_events_within_one_task(outbox: EventOutbox) -> None:
    for topic in ("task.created", "task.started", "task.finished"):
        outbox.record(topic, task_id="task-4")
    outbox.record("task.created", task_id="inny")

    history = outbox.history("task-4")
    assert [event.sequence for event in history] == [1, 2, 3]
    assert [event.topic for event in history] == [
        "task.created", "task.started", "task.finished",
    ]
    # Sequences are per task, not global — a second task starts at one again.
    assert [event.sequence for event in outbox.history("inny")] == [1]


def test_delivery_is_at_least_once_and_consumers_can_say_so(outbox: EventOutbox) -> None:
    """Redelivery after a crash between publish and mark is expected, not a bug."""
    event = outbox.record("task.finished", task_id="task-5")
    again = [event, event]
    assert len(deduplicate(again)) == 1
    assert deduplicate(again)[0].event_id == event.event_id


def test_pruning_keeps_anything_still_unpublished(db, outbox: EventOutbox) -> None:
    outbox.record("task.created", task_id="task-6")
    outbox.drain()
    outbox.record("task.finished", task_id="task-6")

    removed = outbox.prune(keep_after=time.time() + 60)

    assert removed == 1
    assert [event.topic for event in outbox.history("task-6")] == ["task.finished"]


def test_a_store_failure_is_a_typed_error(db) -> None:
    """Persistence failure must be a value the engine can branch on, not a
    message it has to read."""
    with pytest.raises(StoreError):
        # A constraint violation, not a closed handle: the failure a running
        # system actually meets.
        db.execute("INSERT INTO effects(effect_id, capability_id, state, reserved_at)"
                   " VALUES ('dup','a','reserved',1)")
        db.execute("INSERT INTO effects(effect_id, capability_id, state, reserved_at)"
                   " VALUES ('dup','a','reserved',1)")
