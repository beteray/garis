"""GARIS may not claim work it did not do.

Every test here failed against the behaviour shipped in 0.1.2, where asking
"sprawdź, ile miejsca zostało na dysku" on a machine with no API key produced:

    Gotowe: sprawdź, ile miejsca zostało na dysku. Sprawdzone.

Three separate untruths in one sentence. The disk *was* read — that part was
real — but the measured bytes were dropped, the user's own request was played
back as the result, and "Sprawdzone." came from a stub provider that answers
`{"ok": true}` to every prompt it is given, including a request to verify work
nobody had inspected.

The rules these tests hold the engine to:

  * a claim of completion requires an executor that ran and returned;
  * a claim of verification requires a verifier that ran against real evidence;
  * a result is the measurement, never the request repeated back;
  * a missing model blocks honestly instead of producing a cheerful nothing.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from garis.agent import AgentLoop, AgentState, Goal, Planner, Verifier, reflex
from garis.agent.goal import Plan, PlanStep
from garis.agent.report import build_report
from garis.agent.verify import StepEvidence, Verification
from garis.config import Config, ModelsConfig
from garis.models import ModelRouter
from garis.models.providers.fake import FakeProvider, plan_reply, role_aware
from garis.runtime import Runtime
from garis.tasks import TaskState


def build_loop(runtime: Runtime, bus, config: Config, *, providers: list[Any]) -> AgentLoop:
    router = ModelRouter(providers, ModelsConfig(), bus=bus)
    return AgentLoop(
        runtime,
        Planner(router, runtime.registry, memory=runtime.memory, language="pl"),
        Verifier(router, language="pl"),
        bus=bus,
        config=config,
    )


# ------------------------------------------------- the disk question, for real


async def test_disk_question_runs_a_real_disk_inspection(garis) -> None:
    """1, 2, 3. A real tool, real numbers, and the answer is the measurement."""
    task = await garis.do("sprawdź, ile miejsca zostało na dysku", timeout=30)

    assert task.state is TaskState.FINISHED, task.error
    steps = garis.tasks.store.steps_for(task.id)
    assert [s.tool for s in steps] == ["disk_usage"], "inne narzędzie niż odczyt dysku"
    assert steps[0].state.value == "done"

    # The numbers in the record are this machine's, not a fixture's.
    measured = steps[0].result
    truth = shutil.disk_usage(measured["path"])
    assert measured["total"] == truth.total
    assert abs(measured["free"] - truth.free) < 4 * 1024**3  # the disk moves under us

    report = task.report or {}
    assert report["answer"], "raport nie zawiera zmierzonej odpowiedzi"
    assert "GB" in report["short"], f"brak zmierzonych wartości w odpowiedzi: {report['short']}"
    # 3: the request must not come back as the result.
    assert "sprawdź, ile miejsca" not in report["short"].lower()
    assert not report["short"].lower().startswith("gotowe:")


async def test_the_disk_answer_is_built_from_the_numbers_that_came_back() -> None:
    """2, again, at the seam where a sentence could be invented instead."""
    total = 476 * 1024**3
    free = 84_508_000_000
    value = {"path": "C:\\", "total": total, "free": free, "used": total - free,
             "filesystem_used": total - free, "reserved": 0,
             "percent_used": round((total - free) / total * 100, 1)}
    sentence = reflex.answer("disk_usage", value)
    assert "78,7 GB" in sentence and "476,0 GB" in sentence
    assert "83%" in sentence


async def test_a_disk_reading_that_makes_no_sense_is_not_verified() -> None:
    """10. Empty or impossible output cannot pass as a verified measurement."""
    for broken in ({}, {"path": "C:"}, {"total": 0, "free": 0},
                   {"total": 10, "free": 99}, None, "brak"):
        assert reflex.check("disk_usage", broken), f"puste dane uznane za dobre: {broken!r}"
        assert reflex.answer("disk_usage", broken) == ""


# --------------------------------------------------------- no model configured


async def test_no_provider_does_not_manufacture_success(runtime, bus, config) -> None:
    """4 and 6. Nothing to plan with, so nothing is claimed."""
    loop = build_loop(runtime, bus, config, providers=[])

    outcome = await loop.run(Goal("zainstaluj 7-Zip i skonfiguruj skojarzenia"), task_id="t")

    assert outcome.state is AgentState.BLOCKED
    assert not outcome.report.verified
    assert "model" in outcome.report.short.lower()
    assert not outcome.evidence, "nic nie powinno się wykonać bez planu"


async def test_no_provider_still_answers_a_local_question(runtime, bus, config) -> None:
    """5. A trusted local tool does not need a cloud model to read a disk."""
    from garis.tools import system

    system.register(runtime.registry)          # the real disk_usage, on a real disk
    loop = build_loop(runtime, bus, config, providers=[])

    outcome = await loop.run(Goal("ile miejsca zostało na dysku?"), task_id="t")

    assert outcome.state is AgentState.DONE
    assert [e.tool for e in outcome.evidence] == ["disk_usage"]
    assert outcome.report.verified, "odczyt zweryfikowany deterministycznie"
    assert outcome.verification and outcome.verification.checked_by == "rules"
    assert "GB" in outcome.report.short


async def test_a_stub_provider_cannot_certify_anything(runtime, bus, config) -> None:
    """13. The exact defect: a canned reply producing the word „Sprawdzone”."""
    stub = FakeProvider(role_aware(
        plan_reply([{"key": "a", "tool": "look", "params": {},
                     "expects": "widzę stan"}]),
    ))
    loop = build_loop(runtime, bus, config, providers=[stub])

    outcome = await loop.run(Goal("sprawdź stan usługi", criteria=("usługa działa",)),
                             task_id="t")

    assert outcome.verification is not None
    assert outcome.verification.checked_by == "none"
    assert not outcome.report.verified
    assert "Sprawdzone." not in outcome.report.short
    assert "sprawdzone" not in outcome.report.short.lower()


# ------------------------------------------------------- fail-closed semantics


async def test_completion_requires_a_step_that_actually_ran(runtime, bus, config) -> None:
    """7. An empty plan cannot finish. It used to."""
    loop = build_loop(runtime, bus, config, providers=[])
    # A plan with nothing in it, straight past the planner.
    outcome = await loop.run(Goal("zrób coś nieokreślonego"), task_id="t")
    assert outcome.state is not AgentState.DONE


async def test_verification_requires_a_verifier_that_ran() -> None:
    """8. "Nothing failed" is not "checked"."""
    plain = Verification(ok=True, note="nic nie zawiodło", checked_by="none")
    assert not plain.checked

    report = build_report(
        Goal("cokolwiek"),
        Plan(steps=[PlanStep(key="a", tool="look")]),
        [StepEvidence(tool="look", purpose="", ok=True, summary="ok", value={"seen": 1})],
        plain,
    )
    assert not report.verified
    assert "sprawdzone" not in report.short.lower()


async def test_a_crashing_tool_cannot_finish_or_verify(runtime, bus, config) -> None:
    """9. The tool raises; nothing may be claimed."""
    @runtime.registry.tool("explode", "Zawsze wybucha.", params={}, effects=[], category="test")
    async def explode(ctx: Any) -> None:  # pragma: no cover - body never returns
        raise RuntimeError("bum")

    provider = FakeProvider(role_aware(plan_reply([{"key": "a", "tool": "explode",
                                                    "params": {}}])), stub=False)
    loop = build_loop(runtime, bus, config, providers=[provider])

    outcome = await loop.run(Goal("wywołaj to narzędzie"), task_id="t")

    assert outcome.state is AgentState.FAILED
    assert not outcome.report.verified
    assert outcome.report.problems


async def test_shell_success_does_not_prove_the_outcome(runtime, bus, config) -> None:
    """11. Exit code zero is not the postcondition.

    A command that runs cleanly and changes nothing must not produce a verified
    task: there is no evidence about the goal, only about the process.
    """
    @runtime.registry.tool("run_quiet", "Uruchamia polecenie.", params={}, effects=[],
                           category="test")
    async def run_quiet(ctx: Any) -> dict[str, Any]:
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    stub = FakeProvider(role_aware(plan_reply([{"key": "a", "tool": "run_quiet",
                                                "params": {},
                                                "expects": "usługa wystartowała"}])))
    loop = build_loop(runtime, bus, config, providers=[stub])

    outcome = await loop.run(Goal("uruchom usługę", criteria=("usługa odpowiada",)),
                             task_id="t")

    assert not outcome.report.verified
    assert outcome.verification and outcome.verification.checked_by == "none"


# ------------------------------------------------------------------- wording


async def test_no_template_says_checked_without_evidence() -> None:
    """13, at the source. The word is only reachable through a real check."""
    goal = Goal("cokolwiek")
    evidence = [StepEvidence(tool="look", purpose="", ok=True, summary="x", value={"a": 1})]
    plan = Plan(steps=[PlanStep(key="a", tool="look")])

    unchecked = build_report(goal, plan, evidence,
                             Verification(ok=True, note="", checked_by="none"))
    assert "sprawdz" not in unchecked.short.lower().replace("nie sprawdzałem", "")

    checked = build_report(goal, plan, evidence,
                           Verification(ok=True, note="Sprawdziłem rezultat.",
                                        checked_by="model"))
    assert checked.verified


# ------------------------------------------------------------ restart and API


async def test_a_resumed_task_does_not_run_its_finished_steps_again(garis) -> None:
    """14. Recovery re-attaches; it does not repeat an effect already recorded."""
    task = await garis.do("sprawdź, ile miejsca zostało na dysku", timeout=30)
    before = garis.tasks.store.steps_for(task.id)
    assert len(before) == 1

    recovered = await garis.tasks.recover()
    assert all(r.id != task.id for r in recovered), "zakończone zadanie nie jest wznawiane"
    assert len(garis.tasks.store.steps_for(task.id)) == 1


async def test_the_api_reports_the_same_claim_the_engine_made(garis) -> None:
    """12. One canonical status: whatever the engine decided, and nothing else."""
    task = await garis.do("sprawdź, ile miejsca zostało na dysku", timeout=30)
    record = garis.tasks.get(task.id)

    assert record is not None
    payload = record.to_dict()
    assert payload["state"] == "finished"
    assert payload["report"]["verified"] is True
    # The sentence the user sees *is* the measured answer — not a headline with
    # the measurement buried in a detail field nobody renders.
    assert payload["report"]["short"] == payload["report"]["answer"]
    assert "GB" in payload["report"]["answer"]


def test_the_disk_reflex_is_reachable_from_the_words_people_use() -> None:
    for phrasing in (
        "sprawdź, ile miejsca zostało na dysku",
        "ile wolnego miejsca na dysku",
        "ile miejsca zostało na dysku C",
        "how much free space is left on disk",
    ):
        found = reflex.find(phrasing)
        assert found is not None and found.tool == "disk_usage", phrasing

    for other in ("zainstaluj 7-zip", "wyślij fakturę", "cześć"):
        assert reflex.find(other) is None, other


def test_a_model_plan_cannot_claim_to_be_a_reflex() -> None:
    """The deterministic verifier is reachable only from this codebase.

    `Plan.from_dict` is how a model's JSON becomes a plan. If it honoured an
    `origin` field, a completion could ask to be checked by the rules that only
    apply to tools we wrote the validators for.
    """
    smuggled = Plan.from_dict({"origin": reflex.ORIGIN,
                               "steps": [{"key": "a", "tool": "look"}]})
    assert smuggled.origin == "model"


def test_a_temp_directory_is_measured_not_guessed(tmp_path: Path) -> None:
    """The disk numbers come from the filesystem, wherever it is asked about."""
    from garis.tools.system import _disk_bytes

    value = _disk_bytes(str(tmp_path))
    truth = shutil.disk_usage(tmp_path)
    assert value["total"] == truth.total
    assert value["free"] == truth.free
    assert reflex.check("disk_usage", value) == ""

    sentence = reflex.answer("disk_usage", value)
    assert str(tmp_path) in sentence
    assert f"{truth.free / 1024 ** 3:.1f}".replace(".", ",") in sentence
