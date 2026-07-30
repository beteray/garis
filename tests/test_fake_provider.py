"""Guards the test double itself.

``role_aware`` recognises GARIS's prompts by substring because ``models`` must not
import ``agent``. That coupling is invisible: if a prompt is reworded, the fake
would quietly answer every request as chat, and dozens of tests would fail in
confusing ways instead of pointing here. These tests make the breakage obvious.
"""

from __future__ import annotations

from garis.agent.planner import SYSTEM_PROMPT
from garis.agent.verify import VERIFY_PROMPT
from garis.models import Message
from garis.models.providers.fake import (
    PLANNER_MARKER,
    VERIFIED,
    VERIFIER_MARKER,
    FakeProvider,
    plan_reply,
    role_aware,
)


def test_markers_still_match_the_real_prompts() -> None:
    planning = SYSTEM_PROMPT.format(max_steps=10, language="pl")
    verifying = VERIFY_PROMPT.format(language="pl")

    assert PLANNER_MARKER in planning
    assert VERIFIER_MARKER in verifying
    # And they must not match each other, or routing would be ambiguous.
    assert PLANNER_MARKER not in verifying
    assert VERIFIER_MARKER not in planning


def test_role_aware_routes_by_prompt_not_call_order() -> None:
    plan = plan_reply([{"key": "a", "tool": "look", "params": {}}])
    reply = role_aware(plan)

    planning = [Message.system(SYSTEM_PROMPT.format(max_steps=10, language="pl")),
                Message.user("CEL: cokolwiek")]
    verifying = [Message.system(VERIFY_PROMPT.format(language="pl")),
                 Message.user("{}")]

    # Interleaved, as two concurrent tasks would produce.
    assert reply(verifying) == VERIFIED
    assert reply(planning) == plan
    assert reply(verifying) == VERIFIED
    assert reply(planning) == plan


def test_successive_planning_calls_walk_the_list() -> None:
    first = plan_reply([{"key": "a", "tool": "look", "params": {}}], summary="metoda 1")
    second = plan_reply([{"key": "b", "tool": "note", "params": {"text": "x"}}],
                        summary="metoda 2")
    reply = role_aware([first, second])
    planning = [Message.system(SYSTEM_PROMPT.format(max_steps=10, language="pl"))]

    assert reply(planning) == first
    assert reply(planning) == second
    assert reply(planning) == second, "ostatni plan powtarza się, nie kończą się odpowiedzi"


async def test_fake_provider_is_always_available() -> None:
    """GARIS must start and do simple work with no API keys configured."""
    provider = FakeProvider()
    assert provider.available()
    completion = await provider.complete(
        provider.models()[0], [Message.user("cześć")]
    )
    assert completion.text
    assert completion.usage.cost == 0.0


# ---------------------------------------------------------- the no-key fallback


def _planning(goal: str, tools: list[dict[str, object]]) -> list[Message]:
    import json as _json

    return [
        Message.system(SYSTEM_PROMPT.format(max_steps=10, language="pl")),
        Message.system("Dostępne narzędzia (JSON):\n" + _json.dumps(tools, ensure_ascii=False)),
        Message.user(f"CEL: {goal}"),
    ]


CATALOGUE: list[dict[str, object]] = [
    {"name": "disk_usage", "summary": "Sprawdza zajętość dysku dla podanej ścieżki.",
     "params": {"path": {"type": "path"}}, "effects": ["read"]},
    {"name": "package_install", "summary": "Instaluje program z repozytorium.",
     "params": {"package": {"type": "string", "required": True}},
     "effects": ["install", "exec"]},
    {"name": "file_delete", "summary": "Trwale usuwa plik.",
     "params": {"path": {"type": "path", "required": True}},
     "effects": ["delete_permanent"]},
]


async def test_without_a_model_inspection_goals_still_work() -> None:
    """A fresh install with no API key must answer "how much disk is left?"."""
    from garis.models import parse_json_lenient

    provider = FakeProvider()
    completion = await provider.complete(
        provider.models()[0], _planning("ile miejsca zostało na dysku", CATALOGUE)
    )
    plan = parse_json_lenient(completion.text)
    assert [step["tool"] for step in plan["steps"]] == ["disk_usage"]
    assert plan["assumptions"], "musi przyznać, że działa bez modelu"


async def test_the_catalogue_is_read_from_its_own_message() -> None:
    """The planner prompt contains bracketed JSON examples; they must not be parsed."""
    from garis.models.providers.fake import _extract_catalogue

    found = _extract_catalogue(_planning("cokolwiek", CATALOGUE))
    assert [tool["name"] for tool in found] == [t["name"] for t in CATALOGUE]


async def test_without_a_model_change_goals_are_refused_not_faked() -> None:
    """Never report success for work that did not happen.

    Matching "zainstaluj" to a read-only listing tool and calling the goal done
    would be the worst possible failure: a confident lie.
    """
    from garis.models import parse_json_lenient

    provider = FakeProvider()
    for goal in (
        "zainstaluj i skonfiguruj nginx na serwerze",
        "napraw problem z komputerem",
        "wyślij wiadomość do Ani",
        "usuń stare kopie zapasowe",
        "zapłać za domenę",
    ):
        completion = await provider.complete(provider.models()[0], _planning(goal, CATALOGUE))
        plan = parse_json_lenient(completion.text)
        assert plan["steps"] == [], f"{goal!r} nie powinno dostać planu bez modelu"
        assert "klucz" in plan["question"], f"{goal!r} — brak wskazówki o konfiguracji"


async def test_reflex_plan_never_picks_a_mutating_tool() -> None:
    from garis.models import parse_json_lenient

    provider = FakeProvider()
    completion = await provider.complete(
        provider.models()[0],
        _planning("chcę wiedzieć, co z programem", CATALOGUE),
    )
    plan = parse_json_lenient(completion.text)
    for step in plan["steps"]:
        assert step["tool"] == "disk_usage"
