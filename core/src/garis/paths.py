"""Where GARIS keeps its state.

Windows 11 is the primary target (``%LOCALAPPDATA%\\GARIS``); POSIX paths exist so
the core, the server agent and the test suite run anywhere. ``GARIS_HOME``
overrides everything, which is how tests get an isolated instance.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "GARIS_HOME"


def default_home() -> Path:
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "GARIS"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "garis"


@dataclass(frozen=True)
class Paths:
    """Resolved locations for one GARIS instance."""

    home: Path

    @classmethod
    def resolve(cls, home: str | Path | None = None) -> Paths:
        return cls(Path(home).expanduser().resolve() if home else default_home())

    # --- files ---
    @property
    def runtime_file(self) -> Path:
        """Where a *running* engine says it can be reached.

        The desktop shell starts the engine on port 0 and reads the address off
        the pipe, but a shell that opens later — after a crash, a second window,
        an engine somebody started by hand — has no pipe to read. This file is
        how it finds an engine that is already up instead of starting a second
        one on the same database. It holds an address and a pid, never a token.
        """
        return self.home / "runtime.json"

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    @property
    def state_db(self) -> Path:
        """Tasks, audit trail, subscriptions, memory (encrypted at field level)."""
        return self.home / "state.garis-db"

    @property
    def vault_db(self) -> Path:
        """Credentials only. Separate file, separate key, never mixed with memory."""
        return self.home / "vault.garis-db"

    @property
    def key_file(self) -> Path:
        return self.home / "master.key"

    @property
    def lock_file(self) -> Path:
        return self.home / "garis.lock"

    # --- directories ---
    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def cache(self) -> Path:
        return self.home / "cache"

    @property
    def workspace(self) -> Path:
        """Scratch space tasks may write to without asking anyone."""
        return self.home / "workspace"

    @property
    def plugins(self) -> Path:
        return self.home / "plugins"

    @property
    def voices(self) -> Path:
        return self.home / "voices"

    def ensure(self) -> Paths:
        for directory in (self.home, self.logs, self.cache, self.workspace,
                          self.plugins, self.voices):
            directory.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            # Memory and vault live here; keep them out of other users' reach.
            os.chmod(self.home, 0o700)
        return self


__all__ = ["ENV_HOME", "Paths", "default_home"]
