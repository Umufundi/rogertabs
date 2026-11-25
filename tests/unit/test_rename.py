"""The Meet -> Rogertabs rename: env fallbacks, the `meet` alias, credentials, and
carrying an existing ~/.meet install across without losing anything."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from rogertabs import config, launchers, migrate, runtime, update
from rogertabs.identity import credentials

ROOT = Path(__file__).resolve().parents[2]


# ── environment variables: new name first, old name as fallback ─────────


def test_new_env_name_wins_over_the_old_one(monkeypatch):
    monkeypatch.setenv("MEET_REPO", "old")
    assert config.env("REPO") == "old"
    monkeypatch.setenv("ROGERTABS_REPO", "new")
    assert config.env("REPO") == "new"


def test_env_default_when_neither_is_set(monkeypatch):
    monkeypatch.delenv("ROGERTABS_BRANCH", raising=False)
    monkeypatch.delenv("MEET_BRANCH", raising=False)
    assert config.env("BRANCH", "main") == "main"


def test_legacy_meet_home_is_still_honoured(monkeypatch, tmp_path):
    monkeypatch.delenv("ROGERTABS_HOME", raising=False)
    monkeypatch.setenv("MEET_HOME", str(tmp_path / "old-style"))
    assert config.home() == tmp_path / "old-style"
    monkeypatch.setenv("ROGERTABS_HOME", str(tmp_path / "new-style"))
    assert config.home() == tmp_path / "new-style"


def test_default_home_is_dot_rogertabs(monkeypatch, tmp_path):
    monkeypatch.delenv("ROGERTABS_HOME", raising=False)
    monkeypatch.delenv("MEET_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert config.home() == tmp_path / ".rogertabs"


def test_legacy_listener_python_and_model_cache_are_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv("MEET_LISTENER_PYTHON", "/opt/old/python")
    assert runtime.listener_python() == Path("/opt/old/python")
    monkeypatch.setenv("MEET_MODEL_CACHE", str(tmp_path / "mine"))
    env = runtime.listener_env(offline=False)
    assert "ROGERTABS_MODEL_CACHE" not in env  # the user's old setting is not overridden
    assert env["MEET_MODEL_CACHE"] == str(tmp_path / "mine")


def test_listener_reads_either_name(monkeypatch):
    from rogertabs_listen import models

    monkeypatch.delenv("ROGERTABS_MODELS_OFFLINE", raising=False)
    monkeypatch.delenv("MEET_MODELS_OFFLINE", raising=False)
    assert not models.offline()
    monkeypatch.setenv("MEET_MODELS_OFFLINE", "1")
    assert models.offline()
    monkeypatch.delenv("MEET_MODELS_OFFLINE")
    monkeypatch.setenv("ROGERTABS_MODELS_OFFLINE", "1")
    assert models.offline()


# ── credentials: new name, then old, copied forward ─────────────────────


def test_credential_falls_back_to_the_old_name_and_copies_forward(monkeypatch):
    store: dict[str, str] = {"Meet key": "sk-secret"}
    monkeypatch.setattr(credentials, "lookup", lambda service, **k: store.get(service))
    monkeypatch.setattr(
        credentials, "store", lambda service, secret, **k: store.update({service: secret}) or True
    )

    assert credentials.lookup_migrating("Rogertabs key", "Meet key") == "sk-secret"
    assert store == {"Meet key": "sk-secret", "Rogertabs key": "sk-secret"}  # old entry kept


def test_credential_new_name_is_read_first(monkeypatch):
    store = {"Meet key": "old", "Rogertabs key": "new"}
    monkeypatch.setattr(credentials, "lookup", lambda service, **k: store.get(service))
    writes: list[str] = []
    monkeypatch.setattr(credentials, "store", lambda *a, **k: writes.append("x") or True)
    assert credentials.lookup_migrating("Rogertabs key", "Meet key") == "new"
    assert writes == []


def test_credential_missing_everywhere_is_none(monkeypatch):
    monkeypatch.setattr(credentials, "lookup", lambda service, **k: None)
    assert credentials.lookup_migrating("Rogertabs key", "Meet key") is None


def test_credential_survives_a_failed_copy_forward(monkeypatch):
    monkeypatch.setattr(credentials, "lookup", lambda service, **k: "sk" if service == "Meet key" else None)
    monkeypatch.setattr(credentials, "store", lambda *a, **k: False)
    assert credentials.lookup_migrating("Rogertabs key", "Meet key") == "sk"


# ── the `meet` alias ────────────────────────────────────────────────────


def test_meet_alias_prints_one_line_and_forwards(monkeypatch, capsys):
    import meet

    seen: list[list[str]] = []
    monkeypatch.setattr("rogertabs.entry.main", lambda argv: seen.append(argv))
    meet.main(["doctor", "--verbose"])
    assert seen == [["doctor", "--verbose"]]
    assert capsys.readouterr().err.strip() == "Meet is now Rogertabs. Use: rogertabs doctor --verbose"


def test_meet_alias_without_arguments(monkeypatch, capsys):
    import meet

    monkeypatch.setattr("rogertabs.entry.main", lambda argv: None)
    meet.main([])
    assert capsys.readouterr().err.strip() == "Meet is now Rogertabs. Use: rogertabs"


def run_module(module: str, *args: str, home: Path, env_extra: dict | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MEET_", "ROGERTABS_"))}
    env.update({"HOME": str(home), "USERPROFILE": str(home), **(env_extra or {})})
    return subprocess.run([sys.executable, "-P", "-m", module, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_meet_command_really_forwards_to_rogertabs(tmp_path):
    out = run_module("meet", "--help", home=tmp_path, env_extra={"ROGERTABS_HOME": str(tmp_path / "h")})
    assert out.returncode == 0, out.stderr
    assert "Meet is now Rogertabs. Use: rogertabs --help" in out.stderr
    assert "Usage: rogertabs" in out.stdout


def test_rogertabs_command_runs(tmp_path):
    out = run_module("rogertabs", "--help", home=tmp_path, env_extra={"ROGERTABS_HOME": str(tmp_path / "h")})
    assert out.returncode == 0, out.stderr
    assert "Usage: rogertabs" in out.stdout
    assert "Meet is now" not in out.stderr


# ── launchers ───────────────────────────────────────────────────────────


def test_launchers_unix(tmp_path):
    written = launchers.write_launchers(tmp_path / "bin", "/h/.rogertabs/core/bin/python", windows=False)
    assert {p.name for p in written} == {"rogertabs", "meet"}
    assert (tmp_path / "bin" / "rogertabs").read_text() == (
        '#!/bin/sh\nexec "/h/.rogertabs/core/bin/python" -Pm rogertabs "$@"\n')
    assert (tmp_path / "bin" / "meet").read_text().endswith('-Pm meet "$@"\n')
    assert (tmp_path / "bin" / "meet").stat().st_mode & stat.S_IXUSR
    assert launchers.write_launchers(tmp_path / "bin", "/h/.rogertabs/core/bin/python", windows=False) == []


def test_launchers_windows(tmp_path):
    launchers.write_launchers(tmp_path, r"C:\h\.rogertabs\core\Scripts\python.exe", windows=True)
    assert (tmp_path / "rogertabs.cmd").read_bytes() == (
        b'@"C:\\h\\.rogertabs\\core\\Scripts\\python.exe" -Pm rogertabs %*\r\n')
    assert (tmp_path / "meet.cmd").read_bytes().endswith(b"-Pm meet %*\r\n")


# ── moving an existing ~/.meet ──────────────────────────────────────────


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """A fresh HOME with no explicit home chosen, as on a real machine."""
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.delenv("ROGERTABS_HOME", raising=False)
    monkeypatch.delenv("MEET_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def make_old_install(home: Path, *, with_core: bool = False) -> Path:
    old = home / ".meet"
    (old / "meetings" / "2026-11-01-staff").mkdir(parents=True)
    (old / "meetings" / "2026-11-01-staff" / "transcript.md").write_text("hello")
    (old / "voices.db").write_bytes(b"SQLite format 3\0precious")
    (old / "models" / "hf").mkdir(parents=True)
    (old / "models" / "hf" / "weights.bin").write_bytes(b"w" * 1000)
    (old / "runtime").mkdir()
    (old / "runtime" / "manifest.json").write_text('{"models_primed": true}')
    if with_core:
        py = runtime.venv_python(old / "core")
        py.parent.mkdir(parents=True)
        py.write_text("#!/bin/sh\n")
    return old


def test_nothing_pending_without_an_old_home(user_home):
    assert not migrate.pending()


def test_pending_when_only_the_old_home_exists(user_home):
    make_old_install(user_home)
    assert migrate.pending()


def test_an_explicit_home_is_never_migrated(user_home, monkeypatch, tmp_path):
    make_old_install(user_home)
    monkeypatch.setenv("MEET_HOME", str(tmp_path / "mine"))
    assert not migrate.pending()
    monkeypatch.delenv("MEET_HOME")
    monkeypatch.setenv("ROGERTABS_HOME", str(tmp_path / "mine"))
    assert not migrate.pending()


def test_an_existing_new_home_wins_and_nothing_is_merged(user_home):
    make_old_install(user_home)
    (user_home / ".rogertabs").mkdir()
    (user_home / ".rogertabs" / "voices.db").write_bytes(b"newer")
    assert not migrate.pending()


def test_a_new_home_with_no_files_in_it_does_not_block_the_move(user_home):
    make_old_install(user_home)
    (user_home / ".rogertabs" / "models").mkdir(parents=True)  # made by a run that found nothing
    assert migrate.pending()
    migrate.migrate(echo=lambda _: None)
    assert (user_home / ".rogertabs" / "voices.db").read_bytes().endswith(b"precious")


def test_migrate_moves_everything_and_keeps_every_byte(user_home):
    old = make_old_install(user_home)
    result = migrate.migrate(echo=lambda _: None)
    new = user_home / ".rogertabs"
    assert result.moved and result.leftovers == []
    assert (new / "voices.db").read_bytes() == b"SQLite format 3\0precious"
    assert (new / "meetings" / "2026-11-01-staff" / "transcript.md").read_text() == "hello"
    assert (new / "models" / "hf" / "weights.bin").stat().st_size == 1000
    assert not (old / "voices.db").exists()
    assert not migrate.pending()  # idempotent: a second start does nothing


def test_migrate_twice_is_a_no_op(user_home):
    make_old_install(user_home)
    migrate.migrate(echo=lambda _: None)
    before = (user_home / ".rogertabs" / "voices.db").read_bytes()
    assert not migrate.pending()
    migrate.bootstrap([], echo=lambda _: None)
    assert (user_home / ".rogertabs" / "voices.db").read_bytes() == before


def test_launchers_are_rewritten_and_the_old_path_entry_keeps_working(user_home):
    make_old_install(user_home, with_core=True)
    migrate.migrate(echo=lambda _: None)
    new_py = runtime.venv_python(user_home / ".rogertabs" / "core")
    for bin_dir in (user_home / ".rogertabs" / "bin", user_home / ".meet" / "bin"):
        for name in ("rogertabs", "meet"):
            text = (bin_dir / (name + (".cmd" if runtime.IS_WINDOWS else ""))).read_text()
            assert str(new_py) in text and f"-Pm {name}" in text
    assert not any(str(user_home / ".meet" / "core") in p.read_text()
                   for p in (user_home / ".meet" / "bin").iterdir())


def test_cross_device_move_goes_entry_by_entry_and_loses_nothing(user_home, monkeypatch):
    import errno

    old = make_old_install(user_home)
    real_rename = os.rename

    def fake_rename(src, dst, *a, **k):
        if Path(src) == old or Path(src).parent == old:
            raise OSError(errno.EXDEV, "cross-device")  # the folder and its entries both "cross a drive"
        return real_rename(src, dst, *a, **k)

    monkeypatch.setattr(os, "rename", fake_rename)
    result = migrate.migrate(echo=lambda _: None)
    new = user_home / ".rogertabs"
    assert result.leftovers == []
    assert (new / "voices.db").read_bytes() == b"SQLite format 3\0precious"
    assert (new / "meetings" / "2026-11-01-staff" / "transcript.md").read_text() == "hello"
    assert not (new / migrate.MARKER).exists()
    assert not (new / "voices.db.partial").exists()
    assert not old.exists() or not (old / "voices.db").exists()


def test_an_interrupted_move_resumes_without_overwriting(user_home, monkeypatch):
    old = make_old_install(user_home)
    new = user_home / ".rogertabs"

    # The first run dies after moving `meetings` and before the rest.
    new.mkdir()
    (new / migrate.MARKER).write_text("moving")
    (old / "meetings").rename(new / "meetings")
    (new / "meetings" / "2026-11-01-staff" / "extra.md").write_text("written after the first move")
    assert migrate.pending()

    result = migrate.migrate(echo=lambda _: None)
    assert (new / "voices.db").read_bytes().endswith(b"precious")
    assert (new / "models" / "hf" / "weights.bin").exists()
    assert (new / "meetings" / "2026-11-01-staff" / "extra.md").exists()
    assert not (new / migrate.MARKER).exists()
    assert result.leftovers == []


def test_an_entry_that_exists_in_both_is_never_overwritten(user_home):
    old = make_old_install(user_home)
    new = user_home / ".rogertabs"
    new.mkdir()
    (new / migrate.MARKER).write_text("moving")
    (new / "voices.db").write_bytes(b"already moved")
    leftovers = migrate.move_home(old, new)
    assert leftovers == ["voices.db"]
    assert (new / "voices.db").read_bytes() == b"already moved"
    assert (old / "voices.db").read_bytes().endswith(b"precious")  # the other copy is kept too


def test_relaunch_target_when_running_from_the_moved_core(user_home, monkeypatch):
    old = make_old_install(user_home, with_core=True)
    monkeypatch.setattr(sys, "executable", str(runtime.venv_python(old / "core")))
    result = migrate.migrate(echo=lambda _: None)
    assert result.relaunch == runtime.venv_python(user_home / ".rogertabs" / "core")


def test_no_relaunch_when_not_running_from_the_old_home(user_home):
    make_old_install(user_home)
    assert migrate.migrate(echo=lambda _: None).relaunch is None


def test_bootstrap_end_to_end_in_a_real_process(user_home):
    old = make_old_install(user_home)
    out = run_module("rogertabs", "--help", home=user_home)
    assert out.returncode == 0, out.stderr
    assert "moving" in out.stderr
    assert (user_home / ".rogertabs" / "voices.db").read_bytes().endswith(b"precious")
    assert not (old / "voices.db").exists()
    second = run_module("rogertabs", "--help", home=user_home)
    assert "moving" not in second.stderr


# ── the update checkout's remote ────────────────────────────────────────


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("old_url", [
    "https://github.com/Umufundi/meet.git",
    "https://github.com/Umufundi/meet",
    "https://github.com/umufundi/MEET.git/",
    "git@github.com:Umufundi/meet.git",
])
def test_old_origin_is_rewritten(tmp_path, old_url):
    git("init", "-q", cwd=tmp_path)
    git("remote", "add", "origin", old_url, cwd=tmp_path)
    assert update.retarget_origin("git", tmp_path)
    assert git("remote", "get-url", "origin", cwd=tmp_path) == "https://github.com/Umufundi/rogertabs.git"
    assert not update.retarget_origin("git", tmp_path)  # already current


@pytest.mark.parametrize("url", [
    "https://github.com/someone-else/meet.git",
    "/local/mirror/path",
    "https://github.com/Umufundi/rogertabs.git",
])
def test_other_origins_are_left_alone(tmp_path, url):
    git("init", "-q", cwd=tmp_path)
    git("remote", "add", "origin", url, cwd=tmp_path)
    assert not update.retarget_origin("git", tmp_path)
    assert git("remote", "get-url", "origin", cwd=tmp_path) == url


def test_update_default_repo_is_the_renamed_one():
    assert update.REPO == "https://github.com/Umufundi/rogertabs.git"


# ── the listener's renamed package ──────────────────────────────────────


def fake_listener_venv(home: Path, *, legacy: bool) -> Path:
    venv = home / "runtime" / "listener"
    python = runtime.venv_python(venv)
    python.parent.mkdir(parents=True)
    log = home / "pip.log"
    python.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
    python.chmod(0o755)
    if legacy:
        (venv / "lib" / "python3.12" / "site-packages" / "meet_listen").mkdir(parents=True)
    return venv


def test_repair_needed_only_when_the_old_package_is_in_the_venv(user_home):
    home = user_home / ".rogertabs"
    assert not migrate.listener_needs_repair(home / "runtime" / "listener")
    fake_listener_venv(home, legacy=False)
    assert not migrate.listener_needs_repair(home / "runtime" / "listener")


def test_repair_swaps_the_package_and_keeps_the_models_flag(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setenv("ROGERTABS_HOME", str(home))
    venv = fake_listener_venv(home, legacy=True)
    runtime.write_manifest({"listener_digest": "stale", "models_primed": True, "asr_model": "small.en"})
    assert migrate.listener_needs_repair(venv)

    assert migrate.repair_listener(echo=lambda _: None)

    calls = (home / "pip.log").read_text()
    assert "uninstall -y meet-listen" in calls
    assert "--no-deps --force-reinstall" in calls  # no dependency resolution, nothing downloaded
    assert "-c import rogertabs_listen" in calls
    manifest = runtime.read_manifest()
    assert manifest["models_primed"] is True and manifest["asr_model"] == "small.en"
    assert manifest["listener_digest"] == runtime.source_digest()


def test_repair_failure_names_one_command(tmp_path, monkeypatch):
    home = tmp_path / "h"
    monkeypatch.setenv("ROGERTABS_HOME", str(home))
    venv = fake_listener_venv(home, legacy=True)
    python = runtime.venv_python(venv)
    python.write_text('#!/bin/sh\ncase "$*" in *install*) echo "no network" >&2; exit 1;; esac\nexit 0\n')
    lines: list[str] = []
    assert not migrate.repair_listener(echo=lines.append)
    assert any("Run: rogertabs setup" in line for line in lines)


# ── an old install's leftovers ──────────────────────────────────────────


def test_the_installed_meet_package_is_only_the_alias():
    import meet

    names = {p.name for p in Path(meet.__file__).parent.iterdir() if p.name != "__pycache__"}
    assert names == {"__init__.py", "__main__.py"}


def test_purge_removes_an_old_installs_modules_but_keeps_the_alias(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    pkg = site / "meet"
    (pkg / "identity").mkdir(parents=True)
    for name in ("__init__.py", "__main__.py", "cli.py", "update.py"):
        (pkg / name).write_text("")
    (pkg / "identity" / "jev.py").write_text("")
    (site / "meet-0.1.0.dist-info").mkdir()
    (site / "rogertabs-0.1.0.dist-info").mkdir()
    monkeypatch.syspath_prepend(str(site))
    for mod in [m for m in sys.modules if m == "meet" or m.startswith("meet.")]:
        monkeypatch.delitem(sys.modules, mod)

    assert migrate.purge_legacy_package()
    assert sorted(p.name for p in pkg.iterdir()) == ["__init__.py", "__main__.py"]
    assert not (site / "meet-0.1.0.dist-info").exists()
    assert (site / "rogertabs-0.1.0.dist-info").exists()
    assert not migrate.purge_legacy_package()
