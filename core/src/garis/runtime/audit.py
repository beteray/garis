"""Audit trail.

Every action that reaches the runtime is recorded with its intent, the policy
verdict and the outcome — including the ones that were denied or never ran. Two
jobs: answering "co zrobiłeś przez ostatnią godzinę?" without inventing anything,
and making a full-access agent accountable after the fact.

Parameters are redacted on the way in. The audit log must be safe to show the
user, ship in a diagnostic bundle and read out loud.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ..store import Database, dumps, loads
from .action import Action
from .policy import Verdict
from .registry import ToolSpec

REDACTED = "«ukryte»"
MAX_VALUE_CHARS = 300

# Parameter names whose values never belong in a log, even redacted-adjacent ones.
_SECRET_NAME = re.compile(
    r"pass|passwd|haslo|hasło|secret|token|api[_-]?key|klucz|credential|auth|"
    r"cookie|session|private[_-]?key|pin|otp|seed",
    re.IGNORECASE,
)

# Values that look like credentials regardless of the parameter they arrived in.
_SECRET_VALUE = re.compile(
    r"^(sk-|pk_|ghp_|gho_|github_pat_|xox[baprs]-|AIza|ya29\.|eyJ[A-Za-z0-9_-]{10,}\.)"
)


@dataclass(slots=True)
class AuditEntry:
    id: int
    at: float
    tool: str
    intent: str
    decision: str
    outcome: str
    task_id: str | None = None
    effects: tuple[str, ...] = ()
    rule: str | None = None
    params: dict[str, Any] | None = None
    detail: str | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "task_id": self.task_id,
            "tool": self.tool,
            "intent": self.intent,
            "effects": list(self.effects),
            "decision": self.decision,
            "rule": self.rule,
            "params": self.params or {},
            "outcome": self.outcome,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


def redact(params: Any) -> Any:
    """Strip credentials from a parameter tree.

    ``vault://name`` references are kept: they are pointers, not secrets, and
    seeing which credential was used is exactly what an audit is for.
    """
    if isinstance(params, dict):
        out: dict[str, Any] = {}
        for key, value in params.items():
            if _SECRET_NAME.search(str(key)) and not _is_vault_ref(value):
                out[key] = REDACTED
            else:
                out[key] = redact(value)
        return out
    if isinstance(params, (list, tuple)):
        return [redact(v) for v in params]
    if isinstance(params, str):
        if _is_vault_ref(params):
            return params
        if _SECRET_VALUE.match(params.strip()):
            return REDACTED
        if len(params) > MAX_VALUE_CHARS:
            return params[:MAX_VALUE_CHARS] + "…"
        return params
    return params


def _is_vault_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("vault://")


class AuditLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        action: Action,
        spec: ToolSpec | None,
        verdict: Verdict,
        *,
        outcome: str,
        detail: str = "",
        duration_ms: int = 0,
    ) -> int:
        effects = sorted(e.value for e in spec.effects) if spec else []
        cur = self.db.execute(
            "INSERT INTO audit(at, task_id, tool, intent, effects, decision, rule, params,"
            " outcome, detail, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                time.time(),
                action.task_id,
                action.tool,
                action.intent,
                dumps(effects),
                verdict.decision.value,
                verdict.rule,
                dumps(redact(action.params)),
                outcome,
                detail[:2000] if detail else None,
                duration_ms,
            ),
        )
        return int(cur.lastrowid or 0)

    # ------------------------------------------------------------------ reading

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        rows = self.db.query("SELECT * FROM audit ORDER BY at DESC LIMIT ?", (limit,))
        return [_row(r) for r in rows]

    def for_task(self, task_id: str, limit: int = 500) -> list[AuditEntry]:
        rows = self.db.query(
            "SELECT * FROM audit WHERE task_id = ? ORDER BY at LIMIT ?", (task_id, limit)
        )
        return [_row(r) for r in rows]

    def since(self, moment: float, limit: int = 1000) -> list[AuditEntry]:
        rows = self.db.query(
            "SELECT * FROM audit WHERE at >= ? ORDER BY at LIMIT ?", (moment, limit)
        )
        return [_row(r) for r in rows]

    def activity_since(self, moment: float) -> dict[str, Any]:
        """Material for "co robiłeś przez ostatnią godzinę?" — counts, not prose."""
        entries = self.since(moment)
        tools = Counter(e.tool for e in entries)
        failures = [e for e in entries if e.outcome == "error"]
        denied = [e for e in entries if e.decision == "deny"]
        return {
            "from": moment,
            "actions": len(entries),
            "tasks": sorted({e.task_id for e in entries if e.task_id}),
            "top_tools": tools.most_common(8),
            "failures": len(failures),
            "denied": len(denied),
            "waiting_for_approval": sum(1 for e in entries if e.outcome == "awaiting_approval"),
        }

    def prune(self, older_than_days: int = 90) -> int:
        cutoff = time.time() - older_than_days * 86400
        cur = self.db.execute("DELETE FROM audit WHERE at < ?", (cutoff,))
        return cur.rowcount or 0


def _row(row: Any) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        at=row["at"],
        tool=row["tool"],
        intent=row["intent"],
        decision=row["decision"],
        outcome=row["outcome"],
        task_id=row["task_id"],
        effects=tuple(loads(row["effects"], [])),
        rule=row["rule"],
        params=loads(row["params"], {}),
        detail=row["detail"],
        duration_ms=row["duration_ms"],
    )


__all__ = ["REDACTED", "AuditEntry", "AuditLog", "redact"]
