"""Polish survives the trip from the engine to the window.

The failure this replaces is a Windows one: the desktop shell starts the engine
with no console, Python picks ``cp1250`` for ``stdout``, and the first sentence
carrying ``ł`` is either unencodable or unreadable at the other end. Because the
handshake ("API: …", "Token: …") goes down that same pipe, the window can end up
waiting forever for a line that a diacritic killed.

None of this is Windows-specific once the cause is named — an interpreter told to
use ``cp1250`` fails identically on Linux, which is what the subprocess cases
below do.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

from garis.console import use_utf8

# One sentence per shape the product actually prints: status, refusal, provider
# error, a button, a question. Between them they use ł, ą, ę, ó, ż, ź, ć.
POLISH = [
    "GARIS działa.",
    "Nie mam dostępnego modelu.",
    "Klucz odrzucony przez dostawcę.",
    "Zakończ",
    "Czeka na Twoją odpowiedź.",
    "Zażółć gęślą jaźń.",
]


def _stream(encoding: str) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, newline="")


# ----------------------------------------------------------------- the streams


def test_a_stream_that_started_as_cp1250_ends_up_carrying_utf8(monkeypatch) -> None:
    out = _stream("cp1250")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", _stream("cp1250"))

    assert set(use_utf8(environ={})) == {"stdout", "stderr"}

    for sentence in POLISH:
        print(sentence, file=out)
    out.flush()
    written = out.buffer.getvalue()  # type: ignore[attr-defined]

    # The point of the whole file: these bytes are UTF-8, not cp1250.
    assert written.decode("utf-8") == "".join(s + "\n" for s in POLISH)


def test_configuring_the_streams_twice_is_not_an_error(monkeypatch) -> None:
    """``serve`` may re-enter through the CLI; a second call must be a no-op."""
    monkeypatch.setattr(sys, "stdout", _stream("cp1250"))
    monkeypatch.setattr(sys, "stderr", _stream("cp1250"))

    assert set(use_utf8(environ={})) == {"stdout", "stderr"}
    assert use_utf8(environ={}) == (), "drugie wywołanie nie ma czego zmieniać"


def test_a_windowed_process_without_streams_starts_anyway(monkeypatch) -> None:
    """``pythonw`` hands out ``None`` for stdout. That is not a reason to die."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert use_utf8(environ={}) == ()


def test_a_stream_that_cannot_be_reconfigured_is_left_alone(monkeypatch) -> None:
    """Test harnesses and PyInstaller wrap stdout in objects without
    ``reconfigure``; the engine must not care."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert use_utf8(environ={}) == ()


def test_a_stream_that_refuses_to_be_reconfigured_does_not_stop_the_engine(
    monkeypatch,
) -> None:
    class Stubborn(io.TextIOWrapper):
        def reconfigure(self, **kwargs: object) -> None:  # type: ignore[override]
            raise OSError("nie tym razem")

    monkeypatch.setattr(sys, "stdout", Stubborn(io.BytesIO(), encoding="cp1250"))
    monkeypatch.setattr(sys, "stderr", None)
    assert use_utf8(environ={}) == ()


def test_children_of_the_engine_inherit_utf8(monkeypatch) -> None:
    """The engine spawns Python helpers; they must not re-learn cp1250."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    environ: dict[str, str] = {}

    use_utf8(environ=environ)

    assert environ["PYTHONUTF8"] == "1"
    assert environ["PYTHONIOENCODING"].startswith("utf-8")


# ------------------------------------------------------- the whole interpreter


def _run(code: str, encoding: str) -> subprocess.CompletedProcess[bytes]:
    """Run GARIS in a child interpreter that believes it is on a Polish Windows."""
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    env.pop("PYTHONUTF8", None)
    root = Path(__file__).resolve().parents[1] / "core" / "src"
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env)


def test_the_cli_prints_polish_even_when_the_console_says_cp1250() -> None:
    """``garis --help`` is the shortest path from ``main()`` to Polish output."""
    done = _run("import sys; from garis.cli import main; sys.exit(main(['--help']))",
                "cp1250")

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert "Sejf haseł i kluczy" in done.stdout.decode("utf-8")


def test_without_the_fix_the_same_output_is_unreadable() -> None:
    """The control case — proof the test above is measuring something real."""
    done = _run(
        "print('Sejf haseł i kluczy')",
        "cp1250",
    )
    assert done.returncode == 0
    assert b"Sejf has\xb3e" not in done.stdout  # sanity: cp1250 encodes ł as 0xB3
    assert "Sejf haseł" not in done.stdout.decode("utf-8", "replace"), (
        "gdyby to przeszło, cp1250 nie byłoby problemem i test wyżej nic nie mierzy"
    )
