"""Live settings: one writer, one announcement, no restart.

``Config`` knows how to load and save itself. What was missing is the part that
makes a change *arrive*: until now the engine read ``config.json`` once at start,
so every setting the user touched afterwards took effect on the next launch — and
nothing said so.

This module is that missing part, and it is deliberately the only writer:

* :meth:`SettingsService.apply` is what a settings screen calls. It validates on a
  copy, so a typo in the third field cannot leave the first two applied,
* :meth:`SettingsService.reload` picks up a file edited behind the engine's back,
* both end in the same place: values adopted in place (see :meth:`Config.adopt`)
  and one ``config.changed`` event carrying the dotted paths that moved.

Polling rather than filesystem events on purpose: watching a directory portably
means a dependency, a thread and platform-specific failure modes, to notice a
file a person edits by hand perhaps twice a year. A stat call every couple of
seconds costs nothing and behaves identically on Windows, Linux and a network
share.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping
from typing import Any

from .config import Config
from .errors import ConfigError
from .events import EventBus, Topic
from .paths import Paths

POLL_SECONDS = 2.0


class SettingsService:
    """Owns the user's settings while GARIS is running."""

    def __init__(
        self,
        config: Config,
        paths: Paths,
        *,
        bus: EventBus | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.config = config
        self.paths = paths
        self.bus = bus
        self.poll_seconds = poll_seconds
        self._stamp = self._file_stamp()
        self._task: asyncio.Task[None] | None = None
        self._complained = False

    # -------------------------------------------------------------------- write

    def apply(self, changes: Mapping[str, Any], *, source: str = "user") -> tuple[str, ...]:
        """Change settings by dotted path, atomically.

        Validation happens on a copy so a request that is half-wrong is wholly
        rejected — a settings screen must never leave a config in a state the
        user did not ask for.
        """
        candidate = Config.from_dict(self.config.to_dict())
        for path, value in changes.items():
            candidate.set(str(path), value)

        changed = self.config.adopt(candidate)
        if not changed:
            return ()
        self.save()
        self._announce(changed, source)
        return changed

    def save(self) -> None:
        """Persist the current config and remember we are the ones who wrote it."""
        self.config.save(self.paths)
        self._stamp = self._file_stamp()

    # --------------------------------------------------------------------- read

    def reload(self) -> tuple[str, ...]:
        """Adopt the file from disk if it moved. Returns the paths that changed."""
        stamp = self._file_stamp()
        if stamp == self._stamp:
            return ()
        try:
            incoming = Config.load(self.paths)
        except ConfigError as exc:
            # A hand-edited file with a stray comma must not take the engine down,
            # and must not be reported once a second either.
            if not self._complained:
                self._complained = True
                self._notice(f"Nie mogę odczytać ustawień: {exc.explain()}")
            return ()
        self._stamp = stamp
        self._complained = False
        changed = self.config.adopt(incoming)
        if changed:
            self._announce(changed, "disk")
        return tuple(changed)

    # ------------------------------------------------------------------- watch

    async def watch(self) -> None:
        while True:
            await asyncio.sleep(self.poll_seconds)
            with contextlib.suppress(Exception):
                self.reload()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self.watch())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    # ---------------------------------------------------------------- internals

    def _file_stamp(self) -> tuple[float, int]:
        try:
            stat = self.paths.config_file.stat()
        except OSError:
            return (0.0, 0)
        # Size as well as mtime: a coarse filesystem clock can hide an edit made
        # inside the same second as the previous one.
        return (stat.st_mtime, stat.st_size)

    def _announce(self, paths: tuple[str, ...] | list[str], source: str) -> None:
        if self.bus is not None:
            self.bus.emit(Topic.CONFIG_CHANGED, paths=list(paths), source=source)

    def _notice(self, message: str) -> None:
        if self.bus is not None:
            self.bus.emit(Topic.NOTICE, message=message, importance=3)


__all__ = ["POLL_SECONDS", "SettingsService"]
