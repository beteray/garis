"""E1 acceptance: read the real Windows volume through the production path.

Prints the raw fields rather than the sentence GARIS would say, because the
point of this run is to compare numbers with the Windows volume slider — not to
read prose back.

Read-only by construction: it invokes `windows.audio.master.get`, which declares
`effects=frozenset()`, and there is no volume-changing call anywhere in the
audio module. Nothing here can move the slider.

    python tools/e1_audio_check.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from garis import app as garis_app
from garis.kernel.contracts import CapabilityTarget, ExecutionContext
from garis.runtime.action import Action

CAPABILITY = "windows.audio.master.get"


async def main() -> int:
    # No stub provider: this reads a device, not a model, and PRODUCTION
    # rightly refuses to start holding a double.
    garis = garis_app.build()
    try:
        # The production path: target, envelope, policy, executor, evidence,
        # verifier, persistence. Not the adapter.
        result = await garis.runtime.runner.run(
            CapabilityTarget(CAPABILITY),
            Action(tool=CAPABILITY, params={}, task_id="e1", step_key="read"),
            ExecutionContext(task_id="e1", step_key="read",
                             runtime_profile=garis.profile),
        )
        value = result.value if isinstance(result.value, dict) else {}
        report = {
            "platform": sys.platform,
            "ok": result.ok,
            "endpoint_id": value.get("endpoint_id"),
            "endpoint_name": value.get("endpoint_name"),
            "volume_scalar": value.get("volume_scalar"),
            "volume_percent": value.get("volume_percent"),
            "muted": value.get("muted"),
            "verified": result.verified,
            "checked": result.verification.checked,
            "goal_met": result.verification.goal_met,
            "note": result.verification.reason,
            "failure": result.failure.value if result.failure else None,
            "error": result.error,
            "effect_id": result.effect_id,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    finally:
        garis.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
