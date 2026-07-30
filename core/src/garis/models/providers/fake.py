"""Deterministic provider for tests and for running GARIS without any API key.

Two jobs:

* **Tests** — script exact replies, force failures, assert what was asked.
* **First run without keys** — GARIS should still start, plan and execute simple
  goals rather than refusing to work, so the CLI falls back to this provider with
  a small built-in rule set.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from ...errors import ProviderError
from ..base import (
    Capability,
    Completion,
    Job,
    Message,
    ModelSpec,
    Role,
    ToolCall,
    Usage,
)

ALL_JOBS = frozenset(Job)

SPEC = ModelSpec(
    name="fake-1",
    provider="fake",
    jobs=ALL_JOBS,
    capabilities=frozenset({Capability.TOOLS, Capability.JSON_MODE, Capability.STREAMING,
                            Capability.LOCAL}),
    quality=0.3, speed=1.0, input_cost=0.0, output_cost=0.0, local=True,
)

Reply = str | Completion | Callable[[Sequence[Message]], str | Completion]


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        replies: Sequence[Reply] | Reply | None = None,
        *,
        fail_times: int = 0,
        fail_with: str = "symulowana awaria dostawcy",
        spec: ModelSpec = SPEC,
        loop_last: bool = True,
    ) -> None:
        if replies is None:
            queue: list[Reply] = []
        elif isinstance(replies, (str, Completion)) or callable(replies):
            queue = [replies]
        else:
            queue = list(replies)
        self._replies = queue
        self._index = 0
        self._fail_times = fail_times
        self._fail_with = fail_with
        self._spec = spec
        self._loop_last = loop_last
        self.calls: list[list[Message]] = []
        self.tool_payloads: list[Sequence[dict[str, Any]]] = []

    def models(self) -> Sequence[ModelSpec]:
        return (self._spec,)

    def available(self) -> bool:
        return True

    async def complete(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        tools: Sequence[dict[str, Any]] = (),
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
        timeout: float = 120.0,
    ) -> Completion:
        self.calls.append(list(messages))
        self.tool_payloads.append(tools)

        if self._fail_times > 0:
            self._fail_times -= 1
            raise ProviderError(self._fail_with, retryable=True)

        reply = self._next()
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, Completion):
            return reply
        return Completion(
            text=reply,
            model=self._spec.name,
            provider=self.name,
            usage=Usage(input_tokens=_rough_tokens(messages), output_tokens=len(reply) // 4),
        )

    async def stream(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> AsyncIterator[str]:
        completion = await self.complete(model, messages, temperature=temperature,
                                         max_tokens=max_tokens, timeout=timeout)
        for word in completion.text.split(" "):
            yield word + " "

    def _next(self) -> Reply:
        if not self._replies:
            return _reflex_reply
        if self._index < len(self._replies):
            reply = self._replies[self._index]
            self._index += 1
            return reply
        if self._loop_last:
            return self._replies[-1]
        raise ProviderError("Skończyły się zaplanowane odpowiedzi atrapy", retryable=False)

    # --- test helpers ---

    def last_prompt(self) -> str:
        if not self.calls:
            return ""
        return "\n".join(m.content for m in self.calls[-1])

    def last_system_prompt(self) -> str:
        if not self.calls:
            return ""
        return "\n".join(m.content for m in self.calls[-1] if m.role is Role.SYSTEM)


def tool_call_reply(name: str, arguments: dict[str, Any], *, text: str = "") -> Completion:
    """Build a completion that asks for one tool call."""
    return Completion(
        text=text,
        model=SPEC.name,
        provider="fake",
        tool_calls=(ToolCall(id="call-1", name=name, arguments=arguments),),
    )


def plan_reply(steps: Sequence[dict[str, Any]], *, summary: str = "") -> str:
    """Build a planner-shaped JSON reply."""
    return json.dumps(
        {"summary": summary or "plan testowy", "steps": list(steps)}, ensure_ascii=False
    )


VERIFIED = json.dumps({"ok": True, "note": "Sprawdzone.", "unmet": []}, ensure_ascii=False)

# Substrings that identify which of GARIS's prompts arrived. They mirror
# ``agent.planner.SYSTEM_PROMPT`` and ``agent.verify.VERIFY_PROMPT``; this module
# cannot import them (models must not depend on agent), so
# ``test_fake_provider.py`` asserts they still match the real prompts.
PLANNER_MARKER = "planowania GARIS"
VERIFIER_MARKER = "Oceń, czy cel"


def role_aware(
    plans: str | Sequence[str],
    *,
    verification: str = VERIFIED,
    chat: str = "Przyjąłem.",
) -> Callable[[Sequence[Message]], str]:
    """Reply according to *which* prompt arrived, not call order.

    A queue of replies breaks as soon as more than one task shares a provider:
    task two's planning request collides with task one's verification. Routing by
    role keeps one fake provider correct for any number of concurrent tasks, while
    successive planning calls still walk the list, which is how a repair
    ("try another way") is scripted.
    """
    queue = [plans] if isinstance(plans, str) else list(plans)
    used = {"plans": 0}

    def reply(messages: Sequence[Message]) -> str:
        system = "\n".join(m.content for m in messages if m.role is Role.SYSTEM)
        if PLANNER_MARKER in system:
            index = min(used["plans"], len(queue) - 1)
            used["plans"] += 1
            return queue[index]
        if VERIFIER_MARKER in system:
            return verification
        return chat

    return reply


def _reflex_reply(messages: Sequence[Message]) -> str:
    """Bare-minimum useful behaviour when nothing was scripted."""
    last = next((m for m in reversed(messages) if m.role is Role.USER), None)
    text = (last.content if last else "").strip()
    if not text:
        return "Słucham."
    return f"Przyjąłem: {text[:200]}"


def _rough_tokens(messages: Sequence[Message]) -> int:
    return sum(len(m.content) for m in messages) // 4


__all__ = [
    "PLANNER_MARKER",
    "SPEC",
    "VERIFIED",
    "VERIFIER_MARKER",
    "FakeProvider",
    "plan_reply",
    "role_aware",
    "tool_call_reply",
]
