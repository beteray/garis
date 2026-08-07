"""The kernel: task lifecycle, canonical states, and the words every layer shares.

No Windows API calls, no provider code, no frontend states. Anything here is
true of GARIS regardless of what it is running on or which model it is using.
"""

from __future__ import annotations

from .contracts import (
    EFFECTFUL,
    MAX_NESTING_DEPTH,
    CapabilityError,
    CapabilityTarget,
    Effect,
    EffectDisposition,
    EvidenceRecord,
    ExecutionContext,
    Failure,
    Permission,
    PolicyEstimate,
    PolicySubject,
    ProgressEvent,
    Risk,
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
from .recovery import (
    EffectReconciler,
    Observation,
    RecoveryAssessment,
    RecoveryInspection,
    RecoveryRecord,
    RecoveryStatus,
    RecoveryStore,
)

__all__ = [
    "EFFECTFUL",
    "MAX_NESTING_DEPTH",
    "CapabilityError",
    "CapabilityTarget",
    "Effect",
    "EffectDisposition",
    "EffectReconciler",
    "EvidenceRecord",
    "ExecutionContext",
    "Failure",
    "Observation",
    "Permission",
    "PolicyEstimate",
    "PolicySubject",
    "ProgressEvent",
    "RecoveryAssessment",
    "RecoveryInspection",
    "RecoveryRecord",
    "RecoveryStatus",
    "RecoveryStore",
    "Risk",
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
