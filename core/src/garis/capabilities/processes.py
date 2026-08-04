"""Which programs are running — measured, and never inferred.

`windows.process.list` is the first capability, and it is deliberately the
boring one: it exists to establish the shape that `windows.app.launch` and the
Discord workflow will lean on. "Discord is running" must mean "a process named
discord appeared in a list I actually read", not "I started something and the
call returned".

The name carries the `windows.` prefix because that is where it is going to
matter, but the reading works on any host this codebase runs on — the same
capability answers on Linux, and its evidence says which platform produced it so
nothing downstream has to guess.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from ..tools.system import _processes
from .base import Capability, Field, Invocation, Outcome, Permission, Risk, evidence_verifier
from .registry import REGISTRY

MAX_ROWS = 400


async def _list(invocation: Invocation) -> Outcome:
    needle = str(invocation.get("name", "") or "").strip().lower()
    limit = int(invocation.get("limit", 100) or 100)

    rows = await asyncio.to_thread(_processes)
    if not rows:
        # An empty list is not "nothing is running" — no machine has no
        # processes. It means the reading failed, and saying so is the point.
        return Outcome(
            ok=False,
            error="Nie udało się odczytać listy procesów na tym systemie.",
            evidence={"platform": sys.platform, "scanned": 0},
        )

    matched = [r for r in rows if needle in str(r.get("name", "")).lower()] if needle else rows
    trimmed = matched[: max(1, min(limit, MAX_ROWS))]

    return Outcome(
        ok=True,
        value={
            "processes": trimmed,
            "matched": len(matched),
            "scanned": len(rows),
            "query": needle,
        },
        evidence={
            "platform": sys.platform,
            "scanned": len(rows),
            "matched": len(matched),
            # Whether the *named* process is running is answered here, from the
            # measured list, so a caller cannot conclude it from a launch that
            # returned successfully.
            "found": bool(matched) if needle else None,
        },
    )


LIST = REGISTRY.add(
    Capability(
        id="windows.process.list",
        version=1,
        summary="Wypisuje działające procesy, opcjonalnie filtrując po nazwie.",
        risk=Risk.READ,
        permission=Permission.SYSTEM_READ,
        executor=_list,
        verifier=evidence_verifier("Odczytałem listę procesów."),
        inputs=(
            Field("name", "str", "Fragment nazwy procesu", required=False),
            Field("limit", "int", "Ile pozycji zwrócić", required=False),
        ),
        outputs=(
            Field("processes", "list", "Znalezione procesy"),
            Field("matched", "int", "Ile pasuje do zapytania"),
            Field("scanned", "int", "Ile procesów przejrzano"),
        ),
        evidence=(
            Field("platform", "str", "System, na którym mierzono"),
            # A scan of zero processes is a failed reading, not an empty machine.
            Field("scanned", "int", "Ile procesów odczytano",
                  check=lambda value: isinstance(value, int) and value > 0),
        ),
        platforms=("win32", "linux", "darwin"),
        exposed=True,
    )
)


def is_running(performed_value: Any, name: str) -> bool:
    """Did *this* reading contain that process? The only honest way to ask."""
    if not isinstance(performed_value, dict):
        return False
    needle = name.strip().lower()
    return any(
        needle in str(row.get("name", "")).lower()
        for row in performed_value.get("processes", [])
    )


__all__ = ["LIST", "MAX_ROWS", "is_running"]
