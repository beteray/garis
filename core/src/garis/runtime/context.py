"""What a tool handler is given.

Handlers receive a context instead of reaching for globals, for two reasons: they
stay testable without a running GARIS, and every capability they use (nested
actions, secrets, progress reporting) goes through an object the runtime controls.

A tool that needs another tool calls ``ctx.perform`` — which re-enters the same
policy, approval, lease and audit path. There is no side door.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config
from ..events import EventBus, Topic
from ..net import HttpClient
from ..paths import Paths
from ..store import Database
from .action import Action, ActionResult
from .registry import ToolSpec

if TYPE_CHECKING:  # pragma: no cover
    from ..memory import MemoryService
    from ..vault import Vault
    from .executor import Runtime


@dataclass(slots=True)
class ToolContext:
    runtime: Runtime
    action: Action
    spec: ToolSpec
    paths: Paths
    config: Config
    bus: EventBus
    db: Database
    http: HttpClient
    vault: Vault | None = None
    memory: MemoryService | None = None
    deadline: float | None = None
    scratch: dict[str, Any] = field(default_factory=dict)

    # --- identity ---

    @property
    def task_id(self) -> str | None:
        return self.action.task_id

    @property
    def remaining_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    # --- nested capability use ---

    async def perform(self, tool: str, /, *, intent: str = "", **params: Any) -> ActionResult:
        """Run another tool from inside this one, fully policed and audited."""
        nested = Action(
            tool=tool,
            params=params,
            intent=intent or self.action.intent,
            task_id=self.action.task_id,
            step_key=self.action.step_key,
            target=self.action.target,
        )
        return await self.runtime.perform(nested)

    # --- talking to the rest of the system ---

    def progress(self, message: str, **data: Any) -> None:
        """Report progress. Goes to the UI and the task journal, never to speech.

        Quietness is a product rule: work in flight is visible if the user looks,
        and silent if they do not.
        """
        self.bus.emit(
            Topic.TASK_PROGRESS,
            task_id=self.action.task_id,
            tool=self.spec.name,
            message=message,
            **data,
        )

    def note(self, message: str, *, importance: int = 3, **data: Any) -> None:
        """Surface something the user may actually want to hear.

        The notification layer decides whether it is spoken now, held for quiet
        hours or dropped. Tools do not get to interrupt anyone directly.
        """
        self.bus.emit(
            Topic.NOTICE,
            task_id=self.action.task_id,
            source=self.spec.name,
            message=message,
            importance=importance,
            **data,
        )

    # --- secrets ---

    def secret(self, name_or_ref: str) -> str:
        if self.vault is None:
            raise RuntimeError("Sejf nie jest podłączony")
        return self.vault.resolve(
            name_or_ref if name_or_ref.startswith("vault://") else f"vault://{name_or_ref}"
        )

    def has_secret(self, name: str) -> bool:
        return self.vault is not None and self.vault.has(name)

    # --- filesystem conveniences ---

    def workspace(self, *parts: str) -> Path:
        """A scratch directory tools may write to without asking anyone."""
        path = self.paths.workspace.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def task_workspace(self) -> Path:
        path = self.paths.workspace / (self.action.task_id or "adhoc")
        path.mkdir(parents=True, exist_ok=True)
        return path


__all__ = ["ToolContext"]
