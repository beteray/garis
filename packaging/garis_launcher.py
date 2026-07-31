"""Entry point for the frozen engine.

PyInstaller needs a real script to start from. Pointing it straight at
``core/src/garis/cli.py`` would run that module as ``__main__``, which breaks
every relative import inside the package — so this imports the package the
normal way and hands over.

The console script in ``pyproject.toml`` (``garis = "garis.cli:main"``) does the
same thing; this is that same entry point, spelled in a way a build tool can
consume.
"""

from __future__ import annotations

import multiprocessing
import sys

from garis.cli import main

if __name__ == "__main__":
    # A frozen executable re-runs itself to spawn children. Without this the
    # child would start the whole CLI again instead of the worker.
    multiprocessing.freeze_support()
    sys.exit(main())
