"""The agent loop: outcome in, verified result out, errors handled quietly."""

from __future__ import annotations

import json

from garis.agent import AgentLoop, AgentState, Goal, Planner, Verifier
from garis.config import Config, ModelsConfig
from garis.models import ModelRouter
from garis.models.providers.fake import FakeProvider, plan_reply, role_aware
from garis.runtime import Runtime

VERIFIED = json.dumps({"ok": True, "note": "Sprawdzone.", "unmet": []})
NOT_VERIFIED = json.dumps({"ok": False, "note": "Brak potwierdzenia.", "unmet": ["stan"]})


def build_loop(
    runtime: Runtime,
    bus,
    config: Config,
    plans: str | list[str],
    *,
    verification: str = VERIFIED,
) -> tuple[AgentLoop, FakeProvider]:
    # stub=False: this double stands in for a *real* provider. The verifier
    # refuses a verdict from a stub, which is the whole point of the flag — a
    # canned "ok" must never certify anything in production.
    provider = FakeProvider(role_aware(plans, verification=verification), stub=False)
    router = ModelRouter([provider], ModelsConfig(), bus=bus)
    planner = Planner(router, runtime.registry, memory=runtime.memory, language="pl")
    verifier = Verifier(router, language="pl")
    return AgentLoop(runtime, planner, verifier, bus=bus, config=config), provider


# ------------------------------------------------------------------- happy path


async def test_goal_becomes_a_verified_result(runtime: Runtime, bus, config: Config) -> None:
    loop, _ = build_loop(
        runtime,
        bus,
        config,
        plan_reply(
                [
                    {"key": "sprawdz", "tool": "look", "params": {"what": "stan"},
                     "purpose": "rozpoznanie", "expects": "widzę stan"},
                    {"key": "zapisz", "tool": "note", "params": {"text": "raport"},
                     "purpose": "zapis wyniku", "expects": "plik zapisany"},
                ],
                summary="Sprawdzę stan i zapiszę raport.",
            ),
    )

    outcome = await loop.run(Goal("przygotuj raport ze stanu", criteria=("raport istnieje",)),
                             task_id="t1")

    assert outcome.state is AgentState.DONE
    assert outcome.report.verified
    assert outcome.report.short.startswith("Gotowe:")
    assert [e.tool for e in outcome.evidence] == ["look", "note"]
    assert not outcome.report.problems


async def test_planner_only_sees_registered_tools(runtime: Runtime, bus, config) -> None:
    loop, provider = build_loop(runtime, bus, config,
                               plan_reply([{"tool": "look", "params": {}}]))
    await loop.run(Goal("cokolwiek"), task_id="t")
    catalogue = provider.last_system_prompt()
    assert '"note"' in catalogue and '"pay"' in catalogue
    assert "teleport" not in catalogue


async def test_invented_tool_is_dropped_before_execution(
    runtime: Runtime, bus, config
) -> None:
    """A hallucinated tool costs a planning round, never a half-run plan."""
    loop, _ = build_loop(
        runtime,
        bus,
        config,
        plan_reply([
                {"key": "a", "tool": "teleport", "params": {"to": "mars"}},
                {"key": "b", "tool": "look", "params": {}},
            ]),
    )
    outcome = await loop.run(Goal("zrób coś"), task_id="t")
    assert [e.tool for e in outcome.evidence] == ["look"]
    assert "teleport" in outcome.plan.notes


async def test_bad_parameters_in_plan_are_caught_at_planning_time(
    runtime: Runtime, bus, config
) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "note", "params": {"wrong": 1}}]),
    )
    outcome = await loop.run(Goal("zapisz coś"), task_id="t")
    assert outcome.state is AgentState.BLOCKED  # nothing runnable survived
    assert "note" in outcome.plan.notes


# ------------------------------------------------------------- self-healing


async def test_transient_failure_is_retried_without_telling_the_user(
    runtime: Runtime, bus, config: Config, flaky_control
) -> None:
    flaky_control["succeed_on"] = 2      # fails once, then works
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "flaky", "params": {}}]),
    )

    outcome = await loop.run(Goal("użyj kapryśnego narzędzia"), task_id="t")

    assert outcome.state is AgentState.DONE
    assert flaky_control["calls"] == 2
    assert not [e for e in bus.recent("notice")], "użytkownik nie słyszy o błędzie"


async def test_a_dead_end_makes_the_agent_change_method(
    runtime: Runtime, bus, config: Config, flaky_control
) -> None:
    flaky_control["succeed_on"] = 99     # never succeeds
    loop, _ = build_loop(
        runtime,
        bus,
        config,
        [
            plan_reply([{"key": "a", "tool": "flaky", "params": {}}], summary="Metoda 1"),
            plan_reply([{"key": "b", "tool": "look", "params": {}}], summary="Metoda 2")
        ],
    )

    outcome = await loop.run(Goal("osiągnij cel jakkolwiek"), task_id="t")

    assert outcome.state is AgentState.DONE
    assert any(e.tool == "look" and e.ok for e in outcome.evidence)
    assert "zmieniałem metodę" in outcome.report.short


async def test_optional_step_failure_does_not_sink_the_goal(
    runtime: Runtime, bus, config, flaky_control
) -> None:
    flaky_control["succeed_on"] = 99
    loop, _ = build_loop(
        runtime,
        bus,
        config,
        plan_reply([
                {"key": "a", "tool": "flaky", "params": {}, "optional": True},
                {"key": "b", "tool": "look", "params": {}},
            ]),
    )
    outcome = await loop.run(Goal("cel z krokiem opcjonalnym"), task_id="t")
    assert outcome.state is AgentState.DONE
    assert "Pominąłem" in outcome.report.short


# --------------------------------------------------------------- verification


async def test_a_failed_step_cannot_be_reported_as_success(
    runtime: Runtime, bus, config, flaky_control
) -> None:
    """Rules beat the model: if a required step failed, the goal is not met."""
    flaky_control["succeed_on"] = 99
    loop, _ = build_loop(
        runtime,
        bus,
        config,
        [
            plan_reply([{"key": "a", "tool": "flaky", "params": {}}]),
            plan_reply([{"key": "b", "tool": "flaky", "params": {}}]),
            plan_reply([{"key": "c", "tool": "flaky", "params": {}}]),
        ],
    )
    # The verifier would happily say yes; the rules must override it.
    outcome = await loop.run(Goal("nieosiągalny cel"), task_id="t")
    assert outcome.state is AgentState.FAILED
    assert not outcome.report.verified
    assert outcome.report.problems


async def test_verifier_can_reject_a_technically_successful_run(
    runtime: Runtime, bus, config
) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "look", "params": {}, "expects": "usługa działa"}]),
        verification=NOT_VERIFIED,
    )
    outcome = await loop.run(Goal("upewnij się, że usługa działa",
                                 criteria=("usługa odpowiada",)), task_id="t")
    assert outcome.state is AgentState.FAILED
    assert "Brak potwierdzenia" in outcome.report.short


async def test_no_criteria_and_no_failures_skips_the_model_check(
    runtime: Runtime, bus, config
) -> None:
    loop, provider = build_loop(
        runtime, bus, config, plan_reply([{"key": "a", "tool": "look", "params": {}}])
    )
    outcome = await loop.run(Goal("po prostu zajrzyj"), task_id="t")
    assert outcome.state is AgentState.DONE
    # Nothing was declared to check and nothing failed: the step ran, and that is
    # all anyone knows. "Finished" is true; "verified" would not be.
    assert outcome.verification.checked_by == "none"
    assert not outcome.report.verified
    assert "nie sprawdzałem" in outcome.report.short.lower()
    assert len(provider.calls) == 1, "weryfikacja nie powinna kosztować drugiego wywołania"


# ------------------------------------------------------------------- blocking


async def test_approval_parks_the_task_resumably(runtime: Runtime, bus, config) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "pay", "params": {"amount": 30.0}}]),
    )
    outcome = await loop.run(Goal("opłać domenę"), task_id="t")

    assert outcome.state is AgentState.BLOCKED
    assert outcome.resumable and outcome.approval_id
    assert "płatnoś" in outcome.question.lower()


async def test_genuine_missing_information_asks_one_question(
    runtime: Runtime, bus, config
) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        json.dumps({"summary": "", "steps": [],
                    "question": "Na którym serwerze mam to zrobić?"}),
    )
    outcome = await loop.run(Goal("zainstaluj usługę na serwerze"), task_id="t")
    assert outcome.state is AgentState.BLOCKED
    assert outcome.question == "Na którym serwerze mam to zrobić?"


async def test_policy_denial_is_explained_plainly(runtime: Runtime, bus, config, paths) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "note",
                      "params": {"text": str(paths.key_file)}}]),
    )
    outcome = await loop.run(Goal("nadpisz klucz"), task_id="t")
    assert outcome.state is AgentState.FAILED
    assert "zabezpieczeń" in outcome.report.short


async def test_no_model_available_blocks_instead_of_pretending(
    runtime: Runtime, bus, config
) -> None:
    from garis.agent import Planner, Verifier

    empty = ModelRouter([], ModelsConfig())
    loop = AgentLoop(
        runtime,
        Planner(empty, runtime.registry),
        Verifier(empty),
        bus=bus,
        config=config,
    )
    outcome = await loop.run(Goal("cokolwiek"), task_id="t")
    # Blocked, not failed: nothing was attempted, and the thing that is missing
    # is one the user can supply. "Failed" would claim an attempt.
    assert outcome.state is AgentState.BLOCKED
    assert outcome.resumable
    assert "model" in outcome.report.short.lower()
    assert not outcome.report.verified


# ---------------------------------------------------------------- observability


async def test_orb_states_are_emitted_in_order(runtime: Runtime, bus, config) -> None:
    """The UI's animated orb is driven by these events, so the sequence matters."""
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "look", "params": {}}]),
    )
    await loop.run(Goal("zajrzyj", criteria=("widziane",)), task_id="t")

    states = [e.payload["state"] for e in bus.recent("agent.state")]
    assert states[0] == "thinking"
    assert "working" in states and "verifying" in states
    assert states[-1] == "done"


async def test_memory_is_offered_to_the_planner(runtime: Runtime, bus, config, memory) -> None:
    memory.remember("Serwer produkcyjny to 10.0.0.5", subject="serwer produkcyjny",
                    kind="device")
    loop, provider = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "look", "params": {}}]),
    )
    await loop.run(Goal("sprawdź serwer produkcyjny"), task_id="t")
    prompt = provider.last_prompt()
    assert "10.0.0.5" in prompt


async def test_developer_details_are_available_but_not_in_the_short_report(
    runtime: Runtime, bus, config
) -> None:
    loop, _ = build_loop(
        runtime, bus, config,
        plan_reply([{"key": "a", "tool": "look", "params": {"what": "dysk"},
                      "purpose": "sprawdzenie dysku"}]),
    )
    outcome = await loop.run(Goal("sprawdź dysk"), task_id="t")

    assert "look" not in outcome.report.short, "krótki raport mówi o rezultacie, nie o narzędziach"
    assert "look" in outcome.report.details
    assert outcome.report.developer["plan"]["steps"][0]["tool"] == "look"
