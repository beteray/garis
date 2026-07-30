"""Actions: the only currency the runtime accepts.

A model never touches the machine. It produces an ``Action`` — a tool name plus
parameters plus the intent behind it — and the runtime decides what happens
next. Effects are declared by the tool, not inferred from the model's wording,
which is what makes the approval gates trustworthy: a model cannot talk its way
out of "this sends money" by describing it differently.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Effect(StrEnum):
    """What an action does to the world. Declared per tool."""

    READ = "read"                      # inspect files, processes, screen text
    WRITE = "write"                    # create or modify recoverable data
    DELETE_PERMANENT = "delete_permanent"   # irreversible loss
    EXEC = "exec"                      # run a program or script
    INSTALL = "install"                # add or update software
    NETWORK = "network"                # outbound traffic
    PAYMENT = "payment"                # spend money
    PUBLISH = "publish"                # make something publicly visible
    SEND_MESSAGE = "send_message"      # speak as the user to another person
    CREDENTIALS = "credentials"        # touch passwords, tokens, logins
    SYSTEM_CONFIG = "system_config"    # registry, services, firewall, policies
    INPUT_CONTROL = "input_control"    # drive mouse and keyboard
    CAPTURE = "capture"                # screen, microphone, camera
    ELEVATE = "elevate"                # require administrator rights
    REMOTE = "remote"                  # act on another machine

    @property
    def label_pl(self) -> str:
        return _EFFECT_LABELS_PL.get(self, self.value)


_EFFECT_LABELS_PL: dict[Effect, str] = {
    Effect.READ: "odczyt",
    Effect.WRITE: "zapis",
    Effect.DELETE_PERMANENT: "trwałe usunięcie",
    Effect.EXEC: "uruchomienie programu",
    Effect.INSTALL: "instalacja",
    Effect.NETWORK: "połączenie sieciowe",
    Effect.PAYMENT: "płatność",
    Effect.PUBLISH: "publikacja",
    Effect.SEND_MESSAGE: "wysłanie wiadomości",
    Effect.CREDENTIALS: "dane logowania",
    Effect.SYSTEM_CONFIG: "zmiana ustawień systemu",
    Effect.INPUT_CONTROL: "sterowanie myszą i klawiaturą",
    Effect.CAPTURE: "nagrywanie ekranu lub mikrofonu",
    Effect.ELEVATE: "uprawnienia administratora",
    Effect.REMOTE: "działanie na innym urządzeniu",
}


@dataclass(slots=True)
class Action:
    """One request to do something. Immutable in spirit; the runtime never edits it."""

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    intent: str = ""                   # why, in the user's terms — shown in approvals
    task_id: str | None = None
    step_key: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    # Declared consequences the policy layer needs numbers for. Tools may refine
    # these in their own estimator; the planner's guess is a floor, not a cap.
    estimated_bytes: int = 0
    estimated_cost: float = 0.0
    reversible: bool = True
    target: str = "local"              # "local" or a device id for remote work

    def fingerprint(self) -> str:
        """Stable identity of "this exact operation".

        Used to match a stored approval to the retry that follows it, so a task
        resuming after a reboot does not ask the user the same question twice.
        """
        payload = json.dumps(
            {"tool": self.tool, "params": self.params, "target": self.target},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def describe(self) -> str:
        return f"{self.tool}({_short_params(self.params)})"


@dataclass(slots=True)
class ActionResult:
    """Outcome of one action.

    Tool failures are values, not exceptions: the agent loop treats them as input
    to its repair strategy. Only gates that must interrupt the flow — a missing
    approval, a hard denial — are raised.
    """

    ok: bool
    tool: str
    action_id: str
    value: Any = None
    error: str | None = None
    error_kind: str = ""               # "not_found" | "invalid" | "unsupported" | ...
    retryable: bool = True
    detail: str = ""
    duration_ms: int = 0
    verified: bool | None = None       # set when the tool has a verify step

    @classmethod
    def success(cls, action: Action, value: Any = None, *, detail: str = "",
                duration_ms: int = 0, verified: bool | None = None) -> ActionResult:
        return cls(True, action.tool, action.id, value=value, detail=detail,
                   duration_ms=duration_ms, verified=verified)

    @classmethod
    def failure(cls, action: Action, error: str, *, kind: str = "error",
                retryable: bool = True, detail: str = "",
                duration_ms: int = 0) -> ActionResult:
        return cls(False, action.tool, action.id, error=error, error_kind=kind,
                   retryable=retryable, detail=detail, duration_ms=duration_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "action_id": self.action_id,
            "value": self.value,
            "error": self.error,
            "error_kind": self.error_kind,
            "retryable": self.retryable,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "verified": self.verified,
        }


def _short_params(params: dict[str, Any], limit: int = 120) -> str:
    text = ", ".join(f"{k}={_short_value(v)}" for k, v in params.items())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short_value(value: Any, limit: int = 48) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["Action", "ActionResult", "Effect"]
