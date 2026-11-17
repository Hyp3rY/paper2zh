#!/usr/bin/env python3
"""Patch the embedded CPython ``._pth`` file at build time.

The Windows embeddable distribution ignores ``PYTHONPATH`` and does **not**
scan for ``site-packages`` on its own: ``sys.path`` comes exclusively from
``pythonXY._pth``.  Packages installed with ``pip install --target
runtime\\site-packages`` are therefore invisible until that directory is
listed in the ``._pth`` file and ``import site`` is enabled.

Both edits have to happen while the bundle is built -- fixing this at first
run is unreliable, because a broken interpreter has no working package
manager to fix itself with.

Usage:
    python build/_pth_patch.py runtime/python [extra/path ...]

The extra paths are written relative to the interpreter directory with
forward slashes, which CPython accepts on Windows.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SITE_PACKAGES = "../site-packages"

HEADER = "# Patched by build/_pth_patch.py -- do not edit by hand."


def find_pth(python_dir: Path) -> Path:
    """Return the single ``pythonXY._pth`` file inside *python_dir*."""
    candidates = sorted(python_dir.glob("python*._pth"))
    if not candidates:
        raise SystemExit(f"error: no python*._pth found in {python_dir}")
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        raise SystemExit(f"error: ambiguous embedded layout, found several: {names}")
    return candidates[0]


def patch(pth_file: Path, extra_paths: list[str]) -> list[str]:
    """Rewrite *pth_file* idempotently and return the resulting entries."""
    raw = pth_file.read_text(encoding="utf-8")
    entries: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "import site":
            continue
        entries.append(stripped)

    for path in [SITE_PACKAGES, *extra_paths]:
        if path not in entries:
            entries.append(path)

    new_text = f"{HEADER}\n" + "\n".join(entries) + "\nimport site\n"
    if new_text != raw:
        # The file is read before any codec is configured; keep it ASCII.
        new_text.encode("ascii")
        pth_file.write_text(new_text, encoding="ascii", newline="\r\n")
    return entries


def verify(python_exe: Path, expected: list[str]) -> None:
    """Fail the build if the interpreter still cannot see the extra paths."""
    code = "import sys,json; print(json.dumps(sys.path))"
    result = subprocess.run(
        [str(python_exe), "-c", code], capture_output=True, text=True, check=True
    )
    actual = {str(Path(entry).resolve()) for entry in json.loads(result.stdout) if entry}
    wanted = {str((python_exe.parent / path).resolve()) for path in expected}
    missing = sorted(wanted - actual)
    if missing:
        raise SystemExit(f"error: sys.path still misses {missing}: {sorted(actual)}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    python_dir = Path(argv[1]).expanduser().resolve()
    if not python_dir.is_dir():
        raise SystemExit(f"error: not a directory: {python_dir}")

    pth_file = find_pth(python_dir)
    entries = patch(pth_file, argv[2:])
    print(f"[_pth_patch] {pth_file.name}: {entries}")

    python_exe = python_dir / "python.exe"
    if python_exe.exists():
        verify(python_exe, [SITE_PACKAGES, *argv[2:]])
        print("[_pth_patch] verified: site-packages is on sys.path")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
