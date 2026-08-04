"""Provider-agnostic model interface.

The user never names a model, so nothing above this module may mention one. Code
asks for a *need* — "plan this, it is private, keep it cheap" — and the router
answers with a provider. Adding a vendor means adding one file here, not touching
the agent.

Deliberately narrow: chat with optional tool-calling and streaming. Vendor
extras that do not generalise stay behind ``Capability`` flags.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from ..errors import ProviderError


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Capability(StrEnum):
    TOOLS = "tools"                # function calling
    VISION = "vision"              # image input
    JSON_MODE = "json_mode"        # guaranteed-parseable structured output
    STREAMING = "streaming"
    LONG_CONTEXT = "long_context"  # >200k tokens
    REALTIME_VOICE = "realtime_voice"   # native speech-to-speech session
    LOCAL = "local"                # runs on this machine, nothing leaves it


class Job(StrEnum):
    """What the caller is trying to get done. Drives model selection."""

    PLAN = "plan"              # decompose a goal into runnable steps
    REASON = "reason"          # hard analysis, diagnosis, tricky decisions
    CODE = "code"              # write or fix code and scripts
    CHAT = "chat"              # ordinary conversation with the user
    CLASSIFY = "classify"      # short, cheap, high volume (relevance, routing)
    SUMMARIZE = "summarize"
    EXTRACT = "extract"        # pull structured data out of text
    VISION = "vision"
    VOICE = "voice"            # realtime spoken conversation


class Privacy(StrEnum):
    ANY = "any"
    PREFER_LOCAL = "prefer_local"
    LOCAL_ONLY = "local_only"      # never leaves the machine, full stop


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    name: str = ""                              # tool name for TOOL messages
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    images: tuple[bytes, ...] = ()

    @classmethod
    def system(cls, content: str) -> Message:
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str, *, images: Sequence[bytes] = ()) -> Message:
        return cls(Role.USER, content, images=tuple(images))

    @classmethod
    def assistant(cls, content: str, *, tool_calls: Sequence[ToolCall] = ()) -> Message:
        return cls(Role.ASSISTANT, content, tool_calls=tuple(tool_calls))

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> Message:
        return cls(Role.TOOL, content, name=name, tool_call_id=tool_call_id)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cost + other.cost,
        )


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    provider: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    raw: dict[str, Any] | None = None
    #: True when this text came from a stand-in rather than a model — the test
    #: double, or a canned reply. Callers that act on the *content* of a reply
    #: (the verifier above all) must refuse it: a stub that answers `{"ok":
    #: true}` to every prompt certified work nobody had inspected.
    stub: bool = False

    def json(self) -> Any:
        """Parse the reply as JSON, tolerating the ways models wrap it.

        Planners return structured output; models sometimes fence it, prefix it
        with a sentence, or emit trailing commas. Repairing here beats retrying a
        whole plan for a formatting slip.
        """
        return parse_json_lenient(self.text)


@dataclass(slots=True)
class ModelSpec:
    """A concrete model a provider can serve, with the facts the router scores."""

    name: str                       # provider-native id, e.g. "gpt-4.1"
    provider: str
    jobs: frozenset[Job]
    capabilities: frozenset[Capability] = frozenset()
    quality: float = 0.5            # 0..1, relative reasoning strength
    speed: float = 0.5              # 0..1, higher = lower latency
    input_cost: float = 0.0         # per 1M input tokens, in the user's currency
    output_cost: float = 0.0
    context_tokens: int = 128_000
    local: bool = False
    #: Not a model: a deterministic stand-in used by tests and nothing else.
    stub: bool = False

    def supports(self, job: Job) -> bool:
        return job in self.jobs

    def price_for(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_cost + output_tokens * self.output_cost) / 1_000_000


@dataclass(slots=True)
class Need:
    """What the caller needs. The only thing callers are allowed to express."""

    job: Job = Job.CHAT
    privacy: Privacy = Privacy.ANY
    requires: frozenset[Capability] = frozenset()
    max_cost: float | None = None       # per call, hard ceiling
    prefer_speed: bool = False
    min_context: int = 0


class Provider(Protocol):
    """What every vendor adapter implements."""

    name: str

    def models(self) -> Sequence[ModelSpec]: ...

    def available(self) -> bool:
        """True when this provider can actually be called right now (key present)."""
        ...

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
    ) -> Completion: ...

    # Not `async def`: implementations are async *generators*, so the call
    # returns the iterator directly rather than a coroutine yielding one.
    def stream(
        self,
        model: ModelSpec,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> AsyncIterator[str]: ...


_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def parse_json_lenient(text: str) -> Any:
    """Best-effort JSON extraction from a model reply."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1))
    # Widest brace/bracket span — handles a sentence before the object.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, _strip_trailing_commas(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    raise ProviderError(
        f"Odpowiedź modelu nie jest poprawnym JSON-em: {text[:300]}", retryable=True
    )


def _strip_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


__all__ = [
    "Capability",
    "Completion",
    "Job",
    "Message",
    "ModelSpec",
    "Need",
    "Privacy",
    "Provider",
    "Role",
    "ToolCall",
    "Usage",
    "parse_json_lenient",
]
