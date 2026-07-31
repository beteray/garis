#!/usr/bin/env python3
"""Freeze the engine into one self-contained executable and place it as a Tauri sidecar.

Why this exists: the desktop bundle used to ship the shell and nothing else, so
a successful install produced a window that could never connect to anything. The
engine is a Python program; the person installing GARIS should not have to know
that, let alone install Python.

Tauri names sidecars ``<name>-<target-triple>`` and strips the triple when
bundling, leaving a plain ``garis``/``garis.exe`` beside the shell — which is
where ``engine_binary()`` in ``main.rs`` looks first. That is the whole contract
between this script and the shell.

Usage:

    python packaging/build_engine.py            # build for this machine
    python packaging/build_engine.py --clean    # discard intermediates first
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
SIDECAR_DIR = ROOT / "apps" / "desktop" / "src-tauri" / "binaries"
NAME = "garis"


def target_triple() -> str:
    """Ask rustc what this machine is.

    Tauri matches the sidecar filename against this exact string, so deriving it
    from the same tool that will do the matching avoids a class of "works on my
    machine" mismatch (notably msvc vs gnu on Windows).
    """
    try:
        out = subprocess.run(
            ["rustc", "-vV"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "Nie znalazłem `rustc`. Zainstaluj Rusta (https://rustup.rs) i spróbuj ponownie."
        ) from exc

    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("`rustc -vV` nie podał hosta — nie wiem, jak nazwać sidecar.")


def build(clean: bool) -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit("Brakuje PyInstallera. Zainstaluj: pip install pyinstaller") from exc

    work = PACKAGING / "build"
    dist = PACKAGING / "dist"
    if clean:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(dist, ignore_errors=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        NAME,
        # The engine is a CLI the shell talks to over pipes; it needs its stdio.
        # The shell already starts it with CREATE_NO_WINDOW so no console flashes.
        "--console",
        "--noconfirm",
        "--clean" if clean else "--noconfirm",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        # The package lives under core/src, which is not on sys.path for a script.
        "--paths",
        str(ROOT / "core" / "src"),
        # Nothing in the engine imports dynamically, so PyInstaller's static
        # analysis finds the whole tree on its own. These are the exceptions:
        # modules reached only through a lazy import inside a function body on a
        # platform other than the one being analysed.
        "--collect-submodules",
        "garis",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "mypy",
        "--exclude-module",
        "ruff",
        "--exclude-module",
        "tkinter",
        str(PACKAGING / "garis_launcher.py"),
    ]

    print("→ PyInstaller…", flush=True)
    subprocess.run(command, check=True, cwd=ROOT)

    produced = dist / (f"{NAME}.exe" if sys.platform == "win32" else NAME)
    if not produced.is_file():
        raise SystemExit(f"PyInstaller nie wyprodukował {produced}.")
    return produced


def verify(binary: Path) -> None:
    """Run the frozen engine before trusting it.

    A binary that builds and then dies on a missing import is worse than a build
    failure, because the bundle happily ships it.
    """
    print("→ sprawdzam, czy spakowany silnik naprawdę działa…", flush=True)
    result = subprocess.run(
        [str(binary), "doctor"], capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit("Spakowany silnik nie wstaje. Nie kopiuję go do sidecarów.")
    print("  ok — `garis doctor` odpowiada")


def install(binary: Path, triple: str) -> Path:
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    destination = SIDECAR_DIR / f"{NAME}-{triple}{suffix}"
    shutil.copy2(binary, destination)
    destination.chmod(0o755)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Zbuduj silnik GARIS jako sidecar Tauri.")
    parser.add_argument("--clean", action="store_true", help="Wyczyść pliki pośrednie")
    parser.add_argument("--skip-verify", action="store_true", help="Pomiń test spakowanego silnika")
    args = parser.parse_args()

    triple = target_triple()
    print(f"target: {triple}")

    binary = build(args.clean)
    if not args.skip_verify:
        verify(binary)
    destination = install(binary, triple)

    size_mb = destination.stat().st_size / 1_048_576
    print(f"\nGotowe: {destination}  ({size_mb:.1f} MB)")
    print("Teraz `npm run tauri build` w apps/desktop spakuje go razem z powłoką.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
