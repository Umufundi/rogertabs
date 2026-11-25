"""First run after the rename from Meet: carry an existing install across.

Before, everything lived in ~/.meet (%USERPROFILE%\\.meet). Now it lives in
~/.rogertabs. An existing user must keep their voices, meetings and models, so
on the first run of any command this module:

  1. moves ~/.meet to ~/.rogertabs (only if the new folder does not exist, and
     only if no home was chosen explicitly with ROGERTABS_HOME / MEET_HOME);
  2. rewrites the launchers (their absolute paths pointed into the old folder)
     and leaves working `rogertabs` and `meet` launchers in the old ~/.meet/bin,
     so the PATH entry the installer added keeps working;
  3. points the update checkout's `origin` at the renamed repository;
  4. repairs the listener runtime: the package inside its virtualenv was called
     `meet_listen` and is now `rogertabs_listen`, so that one small package is
     swapped (`pip install --no-deps`). The virtualenv, its torch, and every
     model are kept: nothing large is downloaded.

Nothing is ever deleted: voices.db and meetings travel with the folder. The move
is a single atomic rename when it can be. Where it cannot (another drive, or a
Windows process holding files open) it moves entry by entry, behind a marker
file, and a run that is interrupted simply continues on the next start.

Standard library only (plus config/runtime/launchers): this runs before the
rest of the program is imported, and before the old install's modules are safe
to import.
"""

from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# `update` is imported up front, not where it is used: after the folder moves, a
# process running out of the old virtualenv can no longer import anything new.
from . import config, launchers, runtime, update

MARKER = ".migrating-from-meet"
LEGACY_DIST = "meet-listen"

# Modules of the old `meet` package. The installed `meet` package is now only the
# alias (__init__.py, __main__.py); a previous version's other files can linger
# in site-packages after `pip install` of the renamed distribution.
_OLD_MEET_ENTRIES = (
    "cli.py", "config.py", "doctor.py", "events.py", "install.py", "notes.py", "power.py",
    "progress.py", "runtime.py", "sidecar.py", "update.py", "identity", "memory", "session", "ui",
    "_listener",
)


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


@dataclass(slots=True)
class Migration:
    moved: bool = False
    leftovers: list[str] = field(default_factory=list)
    relaunch: Path | None = None


# ── is there anything to do ─────────────────────────────────────────────


def _blank(path: Path) -> bool:
    """A folder tree holding no files at all: created by a run that found
    nothing to migrate, and safe to replace."""
    try:
        return path.is_dir() and not any(p.is_file() or p.is_symlink() for p in path.rglob("*"))
    except OSError:
        return False


def pending(old: Path | None = None, new: Path | None = None) -> bool:
    """True when the old home should be moved into place now."""
    if config.home_override():
        return False  # an explicit home is the user's choice; never touched
    old = old or config.legacy_home()
    new = new or config.default_home()
    if not old.is_dir() or old.resolve() == new.resolve():
        return False
    if (new / MARKER).exists():
        return True  # an earlier run was interrupted part-way
    return not new.exists() or _blank(new)


# ── moving ──────────────────────────────────────────────────────────────


def _move_entry(src: Path, dst: Path) -> None:
    """Move one file, folder or link. Across drives it copies to a `.partial`
    name first and renames, so `dst` is never half-written."""
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    partial = dst.with_name(dst.name + ".partial")
    if partial.is_symlink() or partial.is_file():
        partial.unlink()
    elif partial.exists():
        shutil.rmtree(partial)
    if src.is_symlink():
        os.symlink(os.readlink(src), partial)
    elif src.is_dir():
        shutil.copytree(src, partial, symlinks=True)
    else:
        shutil.copy2(src, partial)
    os.replace(partial, dst)
    # Only now, with the complete copy in place, does the original go.
    if src.is_dir() and not src.is_symlink():
        shutil.rmtree(src)
    else:
        src.unlink()


def move_home(old: Path, new: Path) -> list[str]:
    """Move `old` to `new`. Returns the names that could not be moved (empty
    when everything moved). Safe to call again after an interruption."""
    if _blank(new) and not (new / MARKER).exists():
        shutil.rmtree(new)
    if not new.exists():
        try:
            os.rename(old, new)  # one atomic step: the whole folder, or nothing
            return []
        except OSError:
            pass
    new.mkdir(parents=True, exist_ok=True)
    (new / MARKER).write_text("moving from ~/.meet; safe to delete once the move has finished\n")
    leftovers: list[str] = []
    for entry in sorted(old.iterdir()):
        target = new / entry.name
        if target.exists() or target.is_symlink():
            leftovers.append(entry.name)  # already moved by an earlier run, or in use: never overwrite
            continue
        try:
            _move_entry(entry, target)
        except OSError:
            leftovers.append(entry.name)
    (new / MARKER).unlink(missing_ok=True)
    try:
        old.rmdir()  # only succeeds when it is empty
    except OSError:
        pass
    return leftovers


# ── the repaired surroundings ───────────────────────────────────────────


def _core_python(*homes: Path) -> Path | None:
    for h in homes:
        candidate = runtime.venv_python(h / "core")
        if candidate.exists():
            return candidate
    return None


def _retarget_source(src: Path, echo: Callable[[str], None]) -> None:
    git = shutil.which("git")
    if git is None or not (src / ".git").exists():
        return
    try:
        if update.retarget_origin(git, src):
            echo(f"  update source now points at {update.REPO}")
    except update.UpdateFailed:
        pass


def migrate(echo: Callable[[str], None] = _say) -> Migration:
    """Move the old home across and repair launchers and the update checkout."""
    old, new = config.legacy_home(), config.default_home()
    result = Migration()
    executable = Path(sys.executable)
    try:
        inside = executable.relative_to(old)
    except ValueError:
        inside = None

    echo(f"Rogertabs: moving {old} to {new} (your voices, meetings and models come with it)")
    result.leftovers = move_home(old, new)
    result.moved = True

    core = _core_python(new, old)
    if core is not None:
        launchers.write_launchers(new / "bin", core)
        # The installer put ~/.meet/bin on PATH; keep it working.
        try:
            launchers.write_launchers(old / "bin", core)
        except OSError:
            pass
    _retarget_source(new / "src", echo)

    if result.leftovers:
        echo(f"  still in {old} (in use right now; harmless): {', '.join(result.leftovers)}")
    # This very process may have been running out of the folder just moved.
    if inside is not None and not executable.exists() and (new / inside).exists():
        result.relaunch = new / inside
    echo("  done")
    return result


# ── the listener's renamed package ──────────────────────────────────────


def _site_packages(venv: Path) -> list[Path]:
    found = [venv / "Lib" / "site-packages"]
    found += sorted((venv / "lib").glob("python*/site-packages")) if (venv / "lib").is_dir() else []
    return [p for p in found if p.is_dir()]


def listener_needs_repair(venv: Path | None = None) -> bool:
    """The listener virtualenv still carries the pre-rename `meet_listen` package."""
    venv = venv or runtime.listener_venv()
    if not runtime.venv_python(venv).exists():
        return False
    return any((sp / "meet_listen").is_dir() for sp in _site_packages(venv))


def repair_listener(echo: Callable[[str], None] = _say) -> bool:
    """Swap `meet-listen` for `rogertabs-listen` inside the existing listener
    virtualenv. Dependencies, torch and the models are untouched. True on success."""
    venv = runtime.listener_venv()
    python = runtime.venv_python(venv)
    root = runtime.listener_source()
    if root is None:
        echo("  ! the listener source was not found. Run: rogertabs setup")
        return False
    echo("Rogertabs: updating the listener runtime for the new name (no models are downloaded)")
    pip = [str(python), "-m", "pip", "--disable-pip-version-check"]
    steps = (
        ([*pip, "uninstall", "-y", LEGACY_DIST], False),
        ([*pip, "install", "--no-input", "--quiet", "--no-deps", "--force-reinstall", str(root)], True),
    )
    for argv, required in steps:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                  if runtime.IS_WINDOWS else 0)
        except (OSError, subprocess.SubprocessError) as exc:
            echo(f"  ! {exc}. Run: rogertabs setup")
            return False
        if required and proc.returncode != 0:
            tail = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()][-2:]
            echo(f"  ! could not reinstall the listener ({' / '.join(tail)}). Run: rogertabs setup")
            return False
    check = subprocess.run([str(python), "-c", "import rogertabs_listen"], capture_output=True)
    if check.returncode != 0:
        echo("  ! the listener still does not import. Run: rogertabs setup --force")
        return False
    manifest = runtime.read_manifest()
    if manifest:
        manifest = {k: v for k, v in manifest.items() if k != "manifest_version"}
        manifest["listener_digest"] = runtime.source_digest(root)
        runtime.write_manifest(manifest)
    echo("  done")
    return True


# ── the old `meet` package's leftovers ──────────────────────────────────


def purge_legacy_package() -> bool:
    """Remove the previous version's modules from the installed `meet` package.

    `meet` is now only the transition alias. An old install upgraded in place
    still has its other modules and an old `meet-*.dist-info` next to it; they
    are dead weight and, if pip ever uninstalled that old distribution, would
    take the alias's files with them. True if anything was removed."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("meet")
        package = Path(spec.origin).parent if spec and spec.origin else None
    except (ImportError, ValueError, AttributeError):
        return False
    if package is None or not (package / "cli.py").exists():
        return False  # not an upgraded old install (or a source checkout): nothing stale
    removed = False
    for name in _OLD_MEET_ENTRIES:
        path = package / name
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            else:
                continue
            removed = True
        except OSError:
            pass
    for dist in package.parent.glob("meet-*.dist-info"):
        shutil.rmtree(dist, ignore_errors=True)
    return removed


# ── the one call every entry point makes ────────────────────────────────


def bootstrap(argv: list[str], echo: Callable[[str], None] = _say) -> int | None:
    """Run whatever the rename needs, once. Returns an exit code only when the
    command was re-run in the moved interpreter (the caller then just exits)."""
    purge_legacy_package()
    if pending():
        result = migrate(echo)
        if result.relaunch is not None:
            return subprocess.run([str(result.relaunch), "-P", "-m", "rogertabs", *argv]).returncode
    # Also for an explicit home (a user with MEET_HOME set): the package inside
    # the listener virtualenv was renamed there too.
    if listener_needs_repair():
        repair_listener(echo)
    return None
