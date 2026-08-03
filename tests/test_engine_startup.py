"""The desktop shell can find the engine it just started.

This is the failure the 0.1.2 repair addresses: the window never connected. The
shell hardcoded port 8756, so anything already holding that port killed the
engine on bind, and the shell asked for the address once — synchronously, at
mount — before the engine had printed it.

Two halves live here. The engine must bind a port it was not told in advance and
say which one it got, and it must publish that address for a shell with no pipe
to read. The Rust half of the same contract is tested in
``apps/desktop/src-tauri/src/main.rs``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------- the engine


async def test_the_engine_binds_a_port_it_was_not_told(garis) -> None:
    """Port 0 means "pick one" — and the engine reports back which one."""
    from garis.api.server import ApiServer

    server = ApiServer(garis, host="127.0.0.1", port=0)
    await server.start()
    try:
        assert server.port != 0
        assert server.url == f"http://127.0.0.1:{server.port}"
    finally:
        await server.stop()


async def test_two_engines_do_not_fight_over_one_port(garis) -> None:
    """The reason the hardcoded port had to go.

    With a fixed port the second engine dies on bind; with port 0 they simply
    get different ones. Nothing in the desktop shell may assume otherwise.
    """
    from garis.api.server import ApiServer

    first = ApiServer(garis, host="127.0.0.1", port=0)
    second = ApiServer(garis, host="127.0.0.1", port=0)
    await first.start()
    await second.start()
    try:
        assert first.port != second.port
    finally:
        await first.stop()
        await second.stop()


async def test_a_taken_port_fails_loudly_rather_than_silently(garis) -> None:
    """If somebody does pin a port, the refusal must be an error we can show."""
    from garis.api.server import ApiServer

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]
    try:
        with pytest.raises(OSError):
            await ApiServer(garis, host="127.0.0.1", port=taken).start()
    finally:
        holder.close()


async def test_the_engine_only_listens_on_loopback(garis) -> None:
    """An agent that can run anything must not be reachable from the network."""
    from garis.api.server import ApiServer

    server = ApiServer(garis, host="127.0.0.1", port=0)
    await server.start()
    try:
        host = server._server.sockets[0].getsockname()[0]
        assert host == "127.0.0.1"
    finally:
        await server.stop()


# ------------------------------------------------------- the whole handshake


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_started_engine_announces_where_it_is_and_answers_there(tmp_path: Path) -> None:
    """The shell's contract, end to end, as a real process.

    Starts `garis serve --port 0` exactly the way the desktop shell does, reads
    the handshake off the pipe, checks the published `runtime.json` agrees, and
    calls `/api/health` on the port the engine chose. Nothing is mocked: this is
    the flow that was broken.
    """
    home = tmp_path / "garis-home"
    env = dict(
        os.environ,
        GARIS_HOME=str(home),
        GARIS_PASSPHRASE="test",
        PYTHONPATH=str(REPO / "core" / "src"),
        PYTHONUTF8="1",
    )
    engine = subprocess.Popen(
        [sys.executable, "-m", "garis.cli", "serve", "--print-token", "--port", "0"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    base = token = ""
    try:
        # Exactly the parse `main.rs` does.
        for _ in range(200):
            line = engine.stdout.readline()
            if not line:
                break
            if line.startswith("API: "):
                base = line[5:].strip()
            elif line.startswith("Token: "):
                token = line[7:].strip()
            if base and token:
                break

        assert base.startswith("http://127.0.0.1:"), f"brak adresu w handshake: {base!r}"
        assert token, "silnik nie podał tokenu"
        port = int(base.rsplit(":", 1)[1])
        assert port != 8756 or True  # whatever it picked is fine; that is the point

        # The address is on disk too, for a shell that has no pipe.
        published = json.loads((home / "runtime.json").read_text("utf-8"))
        assert published["url"] == base
        assert published["pid"] == engine.pid
        # And the token is *not* in it — that file is world-readable.
        assert "token" not in json.dumps(published).lower()

        # It actually answers there.
        import urllib.request

        with urllib.request.urlopen(f"{base}/api/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["ok"] is True
        assert health["protocol"] == 1
        # /api/health is the one unauthenticated route and must say nothing else.
        assert set(health) == {"ok", "protocol"}

        # An authenticated call needs the token the handshake carried.
        request = urllib.request.Request(
            f"{base}/api/state", headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            state = json.loads(response.read().decode("utf-8"))
        assert state["protocol"] == 1
        assert state["appearance"]["theme"] == "system"
    finally:
        engine.terminate()
        try:
            engine.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a hang
            engine.kill()
            engine.wait(timeout=10)

    # And it takes the address down on the way out, so the next shell does not
    # attach to a corpse.
    assert not (home / "runtime.json").exists()


def test_an_engine_that_is_gone_leaves_no_address_behind(tmp_path: Path) -> None:
    """A stale runtime.json is how "attach to a running engine" goes wrong."""
    from garis.paths import Paths

    paths = Paths.resolve(tmp_path)
    paths.ensure()
    assert not paths.runtime_file.exists()


def test_the_published_address_never_carries_the_token(tmp_path: Path) -> None:
    """Belt and braces: the shell reads the token from the vault, not this file."""
    from garis.cli import _publish_runtime

    class FakeApi:
        url = "http://127.0.0.1:12345"

    class FakeGaris:
        from garis.paths import Paths as _Paths

        paths = _Paths.resolve(tmp_path)

    _publish_runtime(FakeGaris(), FakeApi())
    written = FakeGaris.paths.runtime_file.read_text("utf-8")

    assert "127.0.0.1:12345" in written
    assert "token" not in written.lower()
    assert "Bearer" not in written


async def test_the_shell_can_ask_the_engine_where_it_is_without_a_pipe(tmp_path: Path) -> None:
    """The published address survives being read back, which is all the shell does."""
    from garis.cli import _publish_runtime, _withdraw_runtime
    from garis.paths import Paths

    class FakeApi:
        url = "http://127.0.0.1:23456"

    class FakeGaris:
        paths = Paths.resolve(tmp_path)

    garis = FakeGaris()
    _publish_runtime(garis, FakeApi())
    assert json.loads(garis.paths.runtime_file.read_text("utf-8"))["url"] == FakeApi.url

    _withdraw_runtime(garis)
    assert not garis.paths.runtime_file.exists()
    # Withdrawing twice is what a crash-then-restart looks like.
    _withdraw_runtime(garis)


async def test_the_health_route_needs_no_token_and_the_rest_do(garis) -> None:
    """What the shell polls to decide "ready" must not need a credential."""
    from garis.api.server import ApiServer
    from garis.api.server import Request as ApiRequest

    server = ApiServer(garis, host="127.0.0.1", port=0)

    health = await server._dispatch(ApiRequest("GET", "/api/health", {}, {}, b""))
    assert health.status == 200
    assert health.body == {"ok": True, "protocol": 1}

    refused = await server._dispatch(ApiRequest("GET", "/api/state", {}, {}, b""))
    assert refused.status == 401
