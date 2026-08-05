"""A temporary bridge for capabilities registered before effects were declared.

Isolated in its own module on purpose. Every capability written from now on
states its effects; this exists only so the ones that predate the rule keep
running while they are migrated, and it says so out loud with a
``DeprecationWarning`` each time it is used.

What it deliberately does **not** do is guess an effect from a permission.
``Permission.FILES`` covers reading a filename and permanently deleting a
directory; ``Permission.PROCESS`` covers listing processes and killing one.
Those need different gates, and a mapping that pretended otherwise would grant a
delete the same silence a read gets. So the fallback reads ``risk`` — the only
declared field that says anything about mutation at all — and produces the
blandest effect that is still honest: "this writes something". Anything that
needs a stronger gate has to say so itself.

Removed in commit 4, together with the last capability that relies on it.
"""

from __future__ import annotations

import warnings

from ..kernel.contracts import Effect, Risk


def needs_migration(risk: Risk, effects: frozenset[Effect]) -> bool:
    """A mutating capability that declared nothing about what it changes."""
    return risk.mutating and not effects


def migrate(capability_id: str, risk: Risk) -> frozenset[Effect]:
    """The fallback, with a complaint attached.

    Returns ``Effect.WRITE`` and nothing else. That is intentionally weak: it
    marks the capability as effectful — so it gets a deterministic effect id and
    an exclusive lease — without inventing a payment, a message or a permanent
    delete that the capability never claimed. Under-declaring is a missing gate;
    over-declaring is a confirmation dialogue for a volume change. Both are
    wrong, and only the capability's author can fix either.
    """
    warnings.warn(
        f"{capability_id}: zdolność zmienia stan ({risk.value}), ale nie deklaruje "
        f"skutków — tymczasowo przyjmuję {Effect.WRITE.value}. Zadeklaruj "
        f"effects= przy rejestracji.",
        DeprecationWarning,
        stacklevel=3,
    )
    return frozenset({Effect.WRITE})


__all__ = ["migrate", "needs_migration"]
