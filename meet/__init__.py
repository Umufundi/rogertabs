"""Transition alias: `meet` is now `rogertabs`.

Kept for one release period so existing habits, scripts, PATH entries and the
launchers an older installer wrote (`python -m meet ...`, which the previous
`meet update` also runs after it pulls new code) keep working. It prints one
line and forwards the arguments to Rogertabs.

Nothing here imports Rogertabs until `main` runs.
"""

from __future__ import annotations

import sys
from typing import NoReturn


def main(argv: list[str] | None = None) -> NoReturn:
    args = list(sys.argv[1:] if argv is None else argv)
    shown = " ".join(args)
    print(f"Meet is now Rogertabs. Use: rogertabs {shown}".rstrip(), file=sys.stderr)

    from rogertabs.entry import main as rogertabs_main

    rogertabs_main(args)
