"""Error taxonomy.

The distinction that matters for GARIS: *recoverable* errors are the agent's own
problem (retry, pick another tool, replan) and must never be reported to the
user, while *blocking* errors need a short, plain-language explanation.
"""

from __future__ import annotations


class GarisError(Exception):
    """Base class. ``blocking`` decides whether the user hears about it."""

    blocking = False
    user_message: str | None = None

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        if user_message is not None:
            self.user_message = user_message

    def explain(self) -> str:
        """Short, non-technical sentence for the user."""
        return self.user_message or str(self)


# --- configuration / wiring -------------------------------------------------


class ConfigError(GarisError):
    blocking = True


class ToolNotFound(GarisError):
    """Requested capability does not exist — the agent should replan."""


class ToolValidationError(GarisError):
    """Parameters did not match the tool's schema — the agent should fix them."""


# --- runtime gates ----------------------------------------------------------


class PolicyDenied(GarisError):
    blocking = True

    def __init__(self, message: str, *, rule: str, user_message: str | None = None) -> None:
        super().__init__(message, user_message=user_message)
        self.rule = rule


class ApprovalRequired(GarisError):
    """Raised when an action needs the user's final go-ahead and none is on file."""

    blocking = True

    def __init__(self, message: str, *, request_id: str, prompt: str) -> None:
        super().__init__(message, user_message=prompt)
        self.request_id = request_id
        self.prompt = prompt


class ApprovalDenied(GarisError):
    blocking = True


class LeaseTimeout(GarisError):
    """Another task holds a resource we need for longer than we can wait."""


class ExecutionError(GarisError):
    """A tool ran but failed. Recoverable by default."""

    def __init__(
        self,
        message: str,
        *,
        tool: str | None = None,
        retryable: bool = True,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.tool = tool
        self.retryable = retryable


class Unsupported(ExecutionError):
    """Capability is not available on this host (wrong OS, missing dependency)."""

    def __init__(self, message: str, *, tool: str | None = None) -> None:
        super().__init__(message, tool=tool, retryable=False)


# --- state ------------------------------------------------------------------


class StoreError(GarisError):
    blocking = True


class LockedError(GarisError):
    """Encrypted state cannot be opened (missing or wrong master key)."""

    blocking = True


class SecretNotAllowed(GarisError):
    """Credentials were handed to plain memory; they belong in the vault."""

    blocking = True
    user_message = "To wygląda na dane logowania — zapisuję je w sejfie, nie w pamięci."


# --- models -----------------------------------------------------------------


class ProviderError(ExecutionError):
    """A model provider failed; the router should fall back to another one."""


class NoModelAvailable(GarisError):
    blocking = True
    user_message = "Nie mam teraz dostępnego modelu, który poradzi sobie z tym zadaniem."


# --- tasks ------------------------------------------------------------------


class TaskAborted(GarisError):
    """User (or supervisor) stopped the task."""


class TaskBlocked(GarisError):
    """Task cannot continue without something from the user."""

    blocking = True


__all__ = [
    "ApprovalDenied",
    "ApprovalRequired",
    "ConfigError",
    "ExecutionError",
    "GarisError",
    "LeaseTimeout",
    "LockedError",
    "NoModelAvailable",
    "PolicyDenied",
    "ProviderError",
    "SecretNotAllowed",
    "StoreError",
    "TaskAborted",
    "TaskBlocked",
    "ToolNotFound",
    "ToolValidationError",
    "Unsupported",
]
