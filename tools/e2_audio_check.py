"""E2 acceptance: change the real Windows volume and prove it by reading it back.

Unlike `e1_audio_check.py`, **this moves the slider.** It sets a level, sets it
back to whatever was there when the script started, and prints every number
involved so a person can compare them with the Windows volume control.

The point is the middle column: `measured` comes from Core Audio *after* the
write, never from the request. A run where `requested` and `measured` disagree by
more than a point is a failing E2, not a rounding note.

    python tools/e2_audio_check.py            # sets 30%, then restores
    python tools/e2_audio_check.py --percent 55
    python tools/e2_audio_check.py --keep     # do not restore the old level
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from garis import app as garis_app
from garis.kernel.contracts import CapabilityTarget, ExecutionContext
from garis.runtime.action import Action

GET = "windows.audio.master.get"
SET = "windows.audio.master.set"


async def _run(garis: Any, capability: str, params: dict[str, Any], step: str) -> Any:
    """The production path: target, envelope, policy, executor, evidence,
    verifier, persistence. Not the adapter."""
    return await garis.runtime.runner.run(
        CapabilityTarget(capability),
        Action(tool=capability, params=params, task_id="e2", step_key=step),
        ExecutionContext(task_id="e2", step_key=step, runtime_profile=garis.profile),
    )


def _summary(result: Any) -> dict[str, Any]:
    value = result.value if isinstance(result.value, dict) else {}
    return {
        "ok": result.ok,
        "requested": value.get("requested_percent"),
        "measured": value.get("volume_percent"),
        "previous": value.get("previous_percent"),
        "muted": value.get("muted"),
        "endpoint_id": value.get("endpoint_id"),
        "verified": result.verified,
        "checked": result.verification.checked,
        "goal_met": result.verification.goal_met,
        "disposition": result.disposition.value,
        "note": result.verification.reason,
        "failure": result.failure.value if result.failure else None,
        "error": result.error,
        "effect_id": result.effect_id,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--percent", type=int, default=30)
    parser.add_argument("--keep", action="store_true",
                        help="zostaw ustawioną głośność zamiast przywracać poprzednią")
    args = parser.parse_args()

    garis = garis_app.build()
    try:
        before = await _run(garis, GET, {}, "read-before")
        was = (before.value or {}).get("volume_percent") if before.ok else None

        changed = await _run(garis, SET, {"percent": args.percent}, "set")

        restored = None
        if not args.keep and isinstance(was, int) and changed.ok:
            # A different step key, so this is its own effect rather than a
            # replay of the one above.
            restored = await _run(garis, SET, {"percent": was}, "restore")

        print(json.dumps(
            {
                "platform": sys.platform,
                "before_percent": was,
                "set": _summary(changed),
                "restored": _summary(restored) if restored is not None else None,
            },
            ensure_ascii=False, indent=2,
        ))
        return 0 if changed.ok and changed.verified else 1
    finally:
        garis.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
