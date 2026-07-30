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
