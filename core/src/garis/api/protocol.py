"""The contract between the engine and every surface: GUI, mobile, server agent.

One shape for everything, so a client written against the desktop works against
the server agent unchanged. Kept deliberately thin — the API translates, it never
decides. Anything that looks like a decision here belongs in ``runtime`` or
``agent``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# Events pushed to clients. Mirrors garis.events.Topic; listed explicitly so a new
# internal topic does not silently become part of the public protocol.
STREAMED_TOPICS = (
    "task.created",
    "task.started",
    "task.step",
    "task.progress",
    "task.finished",
    "task.failed",
    "task.blocked",
    "task.stopped",
    "approval.requested",
    "approval.resolved",
    "agent.state",
    "voice.state",
    "notice",
    "speak",
    "memory.changed",
    "subscription.item",
)


@dataclass(slots=True)
class Reply:
    """An HTTP response before it becomes bytes."""

    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    def encode(self) -> bytes:
        return json.dumps(self.body, ensure_ascii=False, default=str).encode("utf-8")


def ok(body: Any = None, *, status: int = 200) -> Reply:
    return Reply(status, body if body is not None else {"ok": True})


def error(status: int, message: str, *, detail: str = "") -> Reply:
    """Errors carry a sentence a UI can show a person, not a stack trace."""
    payload: dict[str, Any] = {"error": message}
    if detail:
        payload["detail"] = detail
    return Reply(status, payload)


def envelope(topic: str, payload: dict[str, Any], at: float) -> str:
    return json.dumps(
        {"v": PROTOCOL_VERSION, "topic": topic, "at": at, "data": payload},
        ensure_ascii=False,
        default=str,
    )


# --------------------------------------------------------------------- shaping


def task_view(record: Any, *, full: bool = False) -> dict[str, Any]:
    """A task as the UI needs it: outcome first, machinery on request."""
    data = record.to_dict(include_plan=full)
    data["question"] = record.error if record.state.value == "blocked" else ""
    return data


def step_view(step: Any) -> dict[str, Any]:
    return step.to_dict()


def approval_view(request: Any) -> dict[str, Any]:
    return request.to_dict()


def memory_view(record: Any, *, include_content: bool = True) -> dict[str, Any]:
    return record.to_dict(include_content=include_content)


def tool_view(spec: Any) -> dict[str, Any]:
    return {
        "name": spec.name,
        "summary": spec.summary,
        "category": spec.category,
        "effects": sorted(e.value for e in spec.effects),
        "available": spec.supported_here(),
        "reversible": spec.reversible,
    }


def state_view(garis: Any) -> dict[str, Any]:
    """Everything a freshly-opened window needs in one round trip."""
    config = garis.config
    tasks = garis.tasks.store.list(active_only=True)
    return {
        "protocol": PROTOCOL_VERSION,
        "version": _version(),
        "identity": {
            "name": config.identity.name,
            "address_as": config.identity.address_as,
            "language": config.identity.language,
            "onboarded": config.identity.onboarded,
        },
        "persona": {
            "preset": config.persona.preset,
            "brevity": config.persona.brevity,
            "humor": config.persona.humor,
        },
        "voice": {
            "enabled": config.voice.enabled,
            "wake_word": config.voice.wake_word,
            "wake_word_enabled": config.voice.wake_word_enabled,
            "push_to_talk": config.voice.push_to_talk,
            "voice_id": config.voice.voice_id,
        },
        "dev": {
            "verbose": config.dev.verbose,
            "developer_mode": config.dev.developer_mode,
        },
        "counts": {
            "active_tasks": len(tasks),
            "pending_approvals": len(garis.runtime.approvals.pending()),
            "memories": garis.memory.stats()["total"],
            "secrets": len(garis.vault.list()),
        },
        "models": garis.router.describe(),
        "notifications": garis.notifications.describe(),
        "voice_runtime": garis.voice.describe(),
        "capabilities": garis.runtime.describe_capabilities(),
        "active_tasks": [task_view(t) for t in tasks],
    }


def _version() -> str:
    from .. import __version__

    return __version__


__all__ = [
    "PROTOCOL_VERSION",
    "STREAMED_TOPICS",
    "Reply",
    "approval_view",
    "envelope",
    "error",
    "memory_view",
    "ok",
    "state_view",
    "step_view",
    "task_view",
    "tool_view",
]
