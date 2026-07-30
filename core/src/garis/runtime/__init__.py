"""The controlled execution layer.

Public surface for everything above it: build actions, hand them to
:class:`Runtime`, read results. Tools register into :class:`ToolRegistry`;
nothing else may execute.
"""

from .action import Action, ActionResult, Effect
from .approvals import ApprovalBroker, ApprovalRequest, ApprovalState
from .audit import AuditEntry, AuditLog, redact
from .context import ToolContext
from .executor import Runtime
from .leases import Lease, LeaseManager, LeaseMode, app_key, device_key, file_key, host_key
from .policy import Decision, PolicyEngine, Verdict
from .registry import ParamSpec, ToolRegistry, ToolSpec

__all__ = [
    "Action",
    "ActionResult",
    "ApprovalBroker",
    "ApprovalRequest",
    "ApprovalState",
    "AuditEntry",
    "AuditLog",
    "Decision",
    "Effect",
    "Lease",
    "LeaseManager",
    "LeaseMode",
    "ParamSpec",
    "PolicyEngine",
    "Runtime",
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "Verdict",
    "app_key",
    "device_key",
    "file_key",
    "host_key",
    "redact",
]
