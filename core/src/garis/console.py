"""Make the standard streams carry Polish, on every operating system.

Windows is the reason this file exists. A Python process started without a
console — which is exactly how the desktop shell starts the engine — gets
``cp1250`` on ``stdout`` there, not UTF-8. The first sentence containing ``ł``
then either raises ``UnicodeEncodeError`` inside ``print`` or arrives at the
shell as bytes it cannot read. The handshake line the window waits for is
printed by the same stream, so a single Polish character in the wrong place
stops the application from starting at all.

The fix has to run before anything is written, and it has to survive streams
that cannot be reconfigured: ``pythonw`` gives ``None``, PyInstaller wraps the
originals, and tests replace them with ``StringIO``. None of those are errors —
they are simply cases where there is nothing to fix.
"""

from __future__ import annotations

import io
import os
import sys

ENCODING = "utf-8"


def use_utf8(*, environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """Put ``stdout`` and ``stderr`` on UTF-8 and keep child processes there.

    Returns the names of the streams that were actually changed, so a caller
    that wants a diagnostic has facts rather than a guess. Safe to call twice.
    """
    env = os.environ if environ is None else environ
    # Inherited by the sidecar's own children and by a re-exec of ourselves.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = f"{ENCODING}:replace"

    changed = []
    for name in ("stdout", "stderr"):
        if _retune(getattr(sys, name, None)):
            changed.append(name)
    return tuple(changed)


def _retune(stream: object) -> bool:
    """Switch one stream to UTF-8 if it is a stream that can be switched."""
    if stream is None:  # pythonw, or a closed handle
        return False
    if (getattr(stream, "encoding", "") or "").lower().replace("-", "") == "utf8":
        return False

    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return False
    try:
        # errors="replace" so that an unencodable character costs one glyph and
        # not the process: a crash here would take down the whole engine, and
        # the character that caused it would never be seen anyway.
        reconfigure(encoding=ENCODING, errors="replace",
                    line_buffering=True, write_through=True)
    except (ValueError, OSError, io.UnsupportedOperation):
        # A stream that refuses is still a working stream. Losing an accent is
        # survivable; refusing to start is not.
        return False
    return True


__all__ = ["ENCODING", "use_utf8"]
