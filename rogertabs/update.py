"""`rogertabs update`: pull the latest Rogertabs and reinstall it in place.

    rogertabs update            update if there is anything new, then run setup
    rogertabs update --check    only say whether an update is waiting

Rogertabs keeps its own clone of the repository in ~/.rogertabs/src, and Git does the
fetching: for a private fork it already knows how to sign in to GitHub (Git for
Windows remembers the login after one browser sign-in), and nothing here ever
handles a password or token.

This module deliberately imports nothing beyond the standard library and
`rogertabs.config`/`rogertabs.runtime`. On Windows a file that a running process has
loaded cannot be replaced; the updater runs as `python -m rogertabs update`, which
dispatches here before the rest of Rogertabs (numpy, the UI) is ever imported, so
pip is free to replace all of it. `rogertabs setup` then runs in a fresh process,
on the new code, and rebuilds only what changed. Models are never re-downloaded.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import runtime
from .config import env as setting
from .config import home

REPO = setting("REPO", "https://github.com/Umufundi/rogertabs.git")
BRANCH = setting("BRANCH", "main")


class UpdateFailed(RuntimeError):
    def __init__(self, message: str, fix: str = "") -> None:
        super().__init__(message)
        self.fix = fix


def source_dir() -> Path:
    return home() / "src"


def _state_path() -> Path:
    return runtime.runtime_dir() / "source.json"


def installed_commit() -> str | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError):
        return None


def _record(commit: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"commit": commit, "repo": REPO, "branch": BRANCH}), encoding="utf-8")


# What GitHub says when Git sent no login, or a login for an account that
# cannot see the private repository: it answers "not found", not "forbidden".
SIGN_IN_SIGNS = ("repository not found", "authentication failed", "could not read username",
                 "403", "401", "permission denied", "terminal prompts disabled")

SIGN_IN_FIX = (
    "Git is not signed in to a GitHub account that can see this private repository.\n"
    "      Check which account Git uses:   git credential-manager github list\n"
    "      Sign in (opens a browser):      git credential-manager github login\n"
    "      Remove a wrong account first:   git credential-manager github logout <name>\n"
    "      then run `rogertabs update` again. (No `git credential-manager`? `winget upgrade Git.Git`.)"
)


def failure_fix(stderr: str) -> str:
    """The one next step for a failed git command, judged from what git said."""
    text = stderr.lower()
    if any(sign in text for sign in SIGN_IN_SIGNS):
        return SIGN_IN_FIX
    if "could not resolve host" in text or "unable to access" in text:
        return "Check your internet connection and run `rogertabs update` again."
    return "Run `rogertabs update` again; if it keeps failing, send the message above."


def _git(git: str, *args: str, cwd: Path | None = None, interactive: bool = False) -> str:
    """Run git. `interactive` leaves stdout on the terminal (clone progress,
    sign-in prompts, which git writes to the console itself) and still
    captures stderr, so a failure can be explained rather than just echoed."""
    try:
        proc = subprocess.run(
            [git, *args], cwd=str(cwd) if cwd else None, text=True, encoding="utf-8", errors="replace",
            stdout=None if interactive else subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise UpdateFailed(f"git failed: {exc}") from None
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        raise UpdateFailed(
            f"git {args[0]} failed" + (f": {lines[-1]}" if lines else ""), failure_fix(stderr)
        )
    return (proc.stdout or "").strip()


def _is_old_repo_url(url: str) -> bool:
    """The pre-rename repository (https or ssh, with or without `.git`)."""
    tail = url.strip().rstrip("/").lower()
    tail = tail[: -len(".git")] if tail.endswith(".git") else tail
    return tail.endswith("/umufundi/meet") or tail.endswith(":umufundi/meet")


def retarget_origin(git: str, src: Path, repo: str | None = None) -> bool:
    """Point the checkout's `origin` at the renamed repository. GitHub redirects
    the old address, but the redirect is a courtesy that ends the day someone
    creates a new `meet` repository there. A remote that is not the old address
    (a fork, a mirror, a local path) is not ours to touch. True if rewritten."""
    repo = repo or REPO
    try:
        # The configured address, not `remote get-url` (which applies insteadOf rewrites).
        current = _git(git, "config", "--get", "remote.origin.url", cwd=src)
    except UpdateFailed:
        return False
    if current == repo or not _is_old_repo_url(current):
        return False
    _git(git, "remote", "set-url", "origin", repo, cwd=src)
    return True


def _pip_install(src: Path) -> None:
    """Reinstall the core app from the updated source, into this interpreter."""
    pip = [sys.executable, "-m", "pip", "--disable-pip-version-check", "install", "--no-input", "--quiet"]
    for argv, what in (
        ([*pip, "--require-hashes", "--no-deps", "-r", str(src / "requirements.lock")], "dependencies"),
        ([*pip, "--no-deps", "--force-reinstall", str(src)], "Rogertabs"),
    ):
        if subprocess.run(argv).returncode != 0:
            raise UpdateFailed(
                f"installing {what} failed",
                "Check your internet connection and run `rogertabs update` again.",
            )


def _setup() -> int:
    """`rogertabs setup` on the NEW code, in a fresh interpreter.

    `-P` keeps the current folder off sys.path. Without it, running from a
    folder that holds a Rogertabs checkout (an unzipped download, ~/.rogertabs/src)
    silently runs THAT copy of Rogertabs instead of the one just installed.
    """
    return subprocess.run([sys.executable, "-P", "-m", "rogertabs", "setup"]).returncode


# The launchers written before `-P`, exactly as the installers wrote them. They
# are named for the old command and run the old module, which is now the alias.
_OLD_CMD = re.compile(rb'^@"([^"\r\n]+)" -m meet %\*\r\n\Z')
_OLD_SH = re.compile(rb'^#!/bin/sh\nexec "([^"\n]+)" -m meet "\$@"\n\Z')


def upgrade_launcher(bin_dir: Path | None = None) -> bool:
    """Add `-P` to a launcher written by an older installer. True if changed.

    The Windows launcher is a .cmd file, and this code usually runs *from* it:
    cmd.exe reads a running batch file from disk, resuming at the byte offset
    where it left off once Python exits. `-m meet` -> `-Pm meet` makes the
    file exactly one byte longer, so that offset lands on the final newline,
    which cmd reads as a blank line. Any other content is left alone.
    """
    bin_dir = bin_dir or (home() / "bin")
    for name, pattern in (("meet.cmd", _OLD_CMD), ("meet", _OLD_SH)):
        path = bin_dir / name
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not pattern.match(data):
            continue
        new = data.replace(b'" -m meet ', b'" -Pm meet ', 1)
        path.write_bytes(new)
        return True
    return False


def run(
    *,
    check: bool = False,
    force: bool = False,
    echo: Callable[[str], None] = print,
    install: Callable[[Path], None] = _pip_install,
    setup: Callable[[], int] = _setup,
    git: str | None = None,
    repo: str = REPO,
    branch: str = BRANCH,
) -> int:
    git = git or shutil.which("git")
    if not git:
        raise UpdateFailed(
            "Git is needed for `rogertabs update`",
            "Install it with `winget install Git.Git`, open a new PowerShell window, "
            "and run `rogertabs update`.",
        )
    src = source_dir()
    if not (src / ".git").is_dir():
        if src.exists():
            shutil.rmtree(src)
        echo("getting the Rogertabs code (first time only; GitHub may ask you to sign in)")
        _git(git, "clone", "--quiet", "--branch", branch, repo, str(src), interactive=True)
    else:
        if retarget_origin(git, src, repo):
            echo(f"the update source is now {repo}")
        _git(git, "fetch", "--quiet", "origin", branch, cwd=src)

    latest = _git(git, "rev-parse", f"origin/{branch}", cwd=src)
    current = installed_commit()
    if current == latest and not force:
        echo(f"Rogertabs is up to date ({latest[:7]})")
        return 0

    if current:
        try:
            log = _git(git, "log", "--oneline", "--no-merges", "-15", f"{current}..{latest}", cwd=src)
        except UpdateFailed:
            log = ""
        if log:
            echo("what's new:")
            for line in log.splitlines():
                echo(f"  {line[8:]}" if len(line) > 8 else f"  {line}")
    if check:
        echo(f"an update is waiting ({(current or 'unknown')[:7]} -> {latest[:7]}); run `rogertabs update`")
        return 0

    # Our own clone: local edits are never expected, so match the remote exactly.
    _git(git, "reset", "--quiet", "--hard", f"origin/{branch}", cwd=src)
    echo(f"installing Rogertabs {latest[:7]}")
    install(src)
    _record(latest)
    if upgrade_launcher():
        echo("updated the `rogertabs` launcher so it always runs the installed Rogertabs")
    echo("checking the runtime (only what changed is rebuilt; models are kept)")
    return setup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rogertabs update", description="Update Rogertabs to the latest version."
    )
    parser.add_argument("--check", action="store_true", help="only report whether an update is waiting")
    parser.add_argument("--force", action="store_true", help="reinstall even if already up to date")
    args = parser.parse_args(argv)
    try:
        return run(check=args.check, force=args.force)
    except UpdateFailed as exc:
        print(f"  x {exc}", file=sys.stderr)
        if exc.fix:
            print(f"      {exc.fix}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
