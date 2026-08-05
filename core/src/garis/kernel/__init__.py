"""The kernel: task lifecycle, canonical states, and the words every layer shares.

No Windows API calls, no provider code, no frontend states. Anything here is
true of GARIS regardless of what it is running on or which model it is using.
"""

from __future__ import annotations

from .contracts import (
    CapabilityError,
    CapabilityTarget,
    EvidenceRecord,
    ExecutionContext,
    Failure,
    ProgressEvent,
    RuntimeEvent,
    RuntimeProfile,
    StepTarget,
    TaskSnapshot,
    ToolTarget,
    Verification,
    redact,
    target_from_dict,
    target_to_dict,
)

__all__ = [
    "CapabilityError",
    "CapabilityTarget",
    "EvidenceRecord",
    "ExecutionContext",
    "Failure",
    "ProgressEvent",
    "RuntimeEvent",
    "RuntimeProfile",
    "StepTarget",
    "TaskSnapshot",
    "ToolTarget",
    "Verification",
    "redact",
    "target_from_dict",
    "target_to_dict",
]
