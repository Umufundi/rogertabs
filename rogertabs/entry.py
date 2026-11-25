"""The one door every launcher goes through: `rogertabs ...`, `python -m rogertabs`
and the `meet` alias.

Order matters. The rename bootstrap (move an old ~/.meet, repair the listener)
runs first, then `update` is dispatched before anything heavy is imported, so on
Windows pip can replace numpy's DLLs while this process is still alive.
"""

from __future__ import annotations

import sys
from typing import NoReturn


def main(argv: list[str] | None = None) -> NoReturn:
    args = list(sys.argv[1:] if argv is None else argv)

    from . import migrate

    relaunched = migrate.bootstrap(args)
    if relaunched is not None:
        sys.exit(relaunched)

    if args[:1] == ["update"]:
        from .update import main as update_main

        sys.exit(update_main(args[1:]))

    from .cli import app

    app(args=args, prog_name="rogertabs")
    sys.exit(0)
