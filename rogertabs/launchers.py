"""The two tiny launcher scripts the installers and the migration write.

`rogertabs` runs `python -Pm rogertabs`; `meet` runs `python -Pm meet`, the
transition alias that says "Meet is now Rogertabs" and forwards. Both go
through `python -m` rather than the pip-generated console scripts: those carry
an absolute interpreter path and, on Windows, cannot be replaced while running.
`-P` keeps the current folder off sys.path, so a checkout in the working
directory can never shadow the installed copy.

Standard library only: the updater and the migration import this before
anything else is safe to import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

NAMES = ("rogertabs", "meet")  # command name == module it runs


def launcher_text(python: Path | str, module: str, *, windows: bool) -> str:
    if windows:
        return f'@"{python}" -Pm {module} %*\r\n'
    return f'#!/bin/sh\nexec "{python}" -Pm {module} "$@"\n'


def write_launchers(bin_dir: Path, python: Path | str, *, windows: bool | None = None) -> list[Path]:
    """Write `rogertabs` and `meet` launchers into `bin_dir`. Returns the files
    written (a launcher that already has exactly this content is left alone)."""
    windows = (sys.platform == "win32") if windows is None else windows
    bin_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in NAMES:
        path = bin_dir / (f"{name}.cmd" if windows else name)
        text = launcher_text(python, name, windows=windows)
        try:
            if path.read_bytes() == text.encode("ascii", "replace") or path.read_text(
                encoding="utf-8", errors="replace"
            ) == text:
                continue
        except OSError:
            pass
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        if not windows:
            os.chmod(tmp, 0o755)
        os.replace(tmp, path)
        written.append(path)
    return written
