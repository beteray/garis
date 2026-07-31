"""The desktop shell has to be able to find the engine it just started.

`garis serve --print-token` prints where the API is and how to authenticate to
it. The shell reads those two lines off a pipe, while the engine keeps running —
it never exits, so anything that only appears at exit never appears at all.

This is the contract the whole packaged application rests on, and it broke in a
way no unit test could see: Python block-buffers a pipe, so the handshake sat in
a buffer and the window waited for it forever. It looked fine in development
only because that machine happened to export PYTHONUNBUFFERED=1.

Which is why these tests run the engine as a real subprocess, with that variable
deliberately removed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HANDSHAKE_TIMEOUT = 60.0
REPO_ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def unbuffered_free_environment(home: Path) -> dict[str, str]:
    """A subprocess environment that cannot hide a buffering bug.

    Removing PYTHONUNBUFFERED is the entire point: with it set, a broken engine
    passes. A user's machine does not set it.
    """
    environment = dict(os.environ)
    environment.pop("PYTHONUNBUFFERED", None)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["GARIS_HOME"] = str(home)
    environment["PYTHONPATH"] = str(REPO_ROOT / "core" / "src")
    return environment


class Handshake:
    """Reads the engine's opening lines without blocking the test forever."""

    def __init__(self) -> None:
        self.base = ""
        self.token = ""
        self.lines: list[str] = []
        self.complete = threading.Event()

    def read_from(self, stream) -> None:
        for line in stream:
            text = line.rstrip("\n")
            self.lines.append(text)
            if text.startswith("API: "):
                self.base = text.removeprefix("API: ").strip()
            elif "Token: " in text:
                # The token is dimmed with ANSI escapes when stdout is a tty;
                # over a pipe it is plain, but be tolerant either way.
                self.token = text.split("Token: ", 1)[1].strip().replace("\033[0m", "")
            if self.base and self.token:
                self.complete.set()


@pytest.fixture
def engine(tmp_path):
    """A real `garis serve` process, stopped when the test ends."""
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "garis.cli", "serve", "--print-token", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=unbuffered_free_environment(tmp_path / "home"),
        cwd=str(REPO_ROOT),
    )
    handshake = Handshake()
    reader = threading.Thread(target=handshake.read_from, args=(process.stdout,), daemon=True)
    reader.start()
    try:
        yield process, handshake, port
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()


def test_the_shell_learns_where_the_engine_is_without_waiting_for_it_to_exit(engine):
    process, handshake, port = engine

    arrived = handshake.complete.wait(HANDSHAKE_TIMEOUT)

    assert arrived, (
        "Silnik nie przedstawił się w czasie, jaki daje mu powłoka. "
        f"Odebrane linie: {handshake.lines!r}"
    )
    assert process.poll() is None, "Silnik miał się przedstawić i działać dalej, a zakończył się."
    assert handshake.base == f"http://127.0.0.1:{port}"
    assert handshake.token


def test_the_token_the_engine_prints_actually_opens_the_api(engine):
    """A handshake that authenticates nothing would be a handshake in name only."""
    import urllib.error
    import urllib.request

    _, handshake, _port = engine
    assert handshake.complete.wait(HANDSHAKE_TIMEOUT), handshake.lines

    request = urllib.request.Request(
        f"{handshake.base}/api/state",
        headers={"Authorization": f"Bearer {handshake.token}"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200

    # And the same call without it must not be allowed through.
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{handshake.base}/api/state", timeout=20)
    assert refused.value.code == 401
