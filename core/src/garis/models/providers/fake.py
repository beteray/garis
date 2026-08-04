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
from dataclasses import replace
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
    quality=0.3, speed=1.0, input_cost=0.0, output_cost=0.0, local=True, stub=True,
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
        stub: bool = True,
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
        # Marks every reply as a stand-in, which the verifier refuses to treat as
        # a judgement. A test that is deliberately standing in for a *real*
        # provider — "the second vendor answered after the first went down" —
        # passes stub=False and gets a completion that counts.
        self._stub = stub
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
            # Even a hand-built completion from a test is a stand-in: whatever it
            # says, nothing downstream may treat it as a model's judgement.
            return replace(reply, stub=self._stub)
        return Completion(
            text=reply,
            model=self._spec.name,
            provider=self.name,
            usage=Usage(input_tokens=_rough_tokens(messages), output_tokens=len(reply) // 4),
            stub=self._stub,
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
    """Bare-minimum useful behaviour when nothing was scripted.

    Not a toy: this is the path a fresh install takes before any API key exists.
    GARIS should still answer "how much disk is left?" rather than refuse to work,
    so unscripted planning requests get a real, if shallow, plan.
    """
    system = "\n".join(m.content for m in messages if m.role is Role.SYSTEM)
    if PLANNER_MARKER in system:
        return _reflex_plan(messages)
    if VERIFIER_MARKER in system:
        return VERIFIED

    last = next((m for m in reversed(messages) if m.role is Role.USER), None)
    text = (last.content if last else "").strip()
    if not text:
        return "Słucham."
    return f"Przyjąłem: {text[:200]}"


def _reflex_plan(messages: Sequence[Message]) -> str:
    """Pick one safe, parameterless tool that best matches the goal's words.

    Deliberately limited to tools whose required parameters are empty: guessing
    a path or a package name without a real model would be worse than admitting
    the limit. Anything more ambitious asks the user to configure a model.
    """
    catalogue = _extract_catalogue(messages)
    goal = _extract_goal(messages)
    words = _tokens(goal)

    # A goal that asks for a *change* must never be answered with read-only recon
    # and a cheerful "done". Reporting success for work that did not happen is
    # worse than admitting there is no model configured.
    if words & _CHANGE_INTENT:
        return _needs_a_model()

    best: tuple[float, dict[str, Any] | None] = (0.0, None)
    for tool in catalogue:
        required = [
            name for name, schema in (tool.get("params") or {}).items()
            if isinstance(schema, dict) and schema.get("required")
        ]
        if required:
            continue
        effects = set(tool.get("effects") or [])
        if not effects <= {"read", "network"}:
            continue  # never guess your way into a change
        haystack = _tokens(f"{tool.get('name', '')} {tool.get('summary', '')}")
        score = sum(1.0 for word in words if word in haystack)
        if score > best[0]:
            best = (score, tool)

    if best[1] is None or best[0] < 1:
        return _needs_a_model()

    tool = best[1]
    return json.dumps(
        {
            "summary": f"Sprawdzę to narzędziem {tool['name']}.",
            "assumptions": ["Działam bez modelu AI, więc wykonuję samo rozpoznanie."],
            "steps": [
                {
                    "key": "rozpoznanie",
                    "tool": tool["name"],
                    "params": {},
                    "purpose": tool.get("summary", ""),
                    "expects": "dane odczytane",
                }
            ],
            "question": "",
        },
        ensure_ascii=False,
    )


def _extract_catalogue(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Find the tool catalogue among the system messages.

    Scans each message separately rather than the concatenation: the planner's
    instructions contain bracketed JSON *examples*, so a first-``[``-to-last-``]``
    span over the joined text picks up prose and parses as nothing.
    """
    for message in messages:
        if message.role is not Role.SYSTEM:
            continue
        start = message.content.find("[")
        end = message.content.rfind("]")
        if start < 0 or end <= start:
            continue
        try:
            parsed = json.loads(message.content[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            tools = [t for t in parsed if isinstance(t, dict) and "name" in t]
            if tools:
                return tools
    return []


def _extract_goal(messages: Sequence[Message]) -> str:
    for message in messages:
        if message.role is Role.USER and message.content.startswith("CEL:"):
            return message.content[4:].strip()
    last = next((m for m in reversed(messages) if m.role is Role.USER), None)
    return last.content if last else ""


def _needs_a_model() -> str:
    return json.dumps(
        {
            "summary": "",
            "steps": [],
            "question": "Bez skonfigurowanego modelu AI poradzę sobie tylko z prostym "
                        "rozpoznaniem, a to zadanie wymaga realnych zmian. Dodaj klucz "
                        "(garis vault set openai_api_key) i zlec je ponownie — wykonam "
                        "je w całości.",
        },
        ensure_ascii=False,
    )


_STOPWORDS = frozenset(
    {
        "jak", "ile", "czy", "dla", "the", "and", "mnie", "mam", "moje", "moja", "moj",
        "sprawdz", "pokaz", "zrob", "prosze", "chce", "teraz", "tego", "tym", "sie",
    }
)

# Verb stems (5 chars, diacritic-free — matching ``_tokens``) that mean the user
# wants something changed, not merely inspected.
_CHANGE_INTENT = frozenset(
    {
        "zains", "insta", "skonf", "konfi", "napra", "wdroz", "usun", "wysla", "wysli",
        "zmien", "utwor", "stwor", "zakti", "zaktu", "przen", "skasu", "wylac", "wlacz",
        "resta", "uruch", "zatrz", "kupic", "zamow", "oplac", "zapla", "publi", "deplo",
        "confi", "creat", "delet", "remov", "updat", "upgra", "fix", "send", "buy",
    }
)


def _tokens(text: str) -> set[str]:
    """Diacritic-insensitive word stems, so "dysku" matches "disk_usage"'s summary."""
    import re
    import unicodedata

    flat = "".join(
        ch for ch in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(ch)
    )
    words = {w for w in re.split(r"[^a-z0-9]+", flat) if len(w) > 2 and w not in _STOPWORDS}
    return words | {w[:5] for w in words}


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
