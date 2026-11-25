#!/usr/bin/env sh
# Install Rogertabs on macOS or Linux: core app, `rogertabs` on PATH, then `rogertabs setup`.
#
#   sh scripts/install.sh [--skip-setup]
#
# Mirrors scripts/install.ps1. Everything lands under ~/.rogertabs (or $ROGERTABS_HOME;
# the old $MEET_HOME is still honoured); nothing is installed system-wide.
#
# Upgrading from Meet: an existing ~/.meet (voices, meetings, models) is moved to
# ~/.rogertabs first, so nothing is lost and nothing is downloaded again.
set -eu

SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
LEGACY="$HOME/.meet"
MOVED=""
if [ -z "${ROGERTABS_HOME:-}" ] && [ -z "${MEET_HOME:-}" ] && [ -d "$LEGACY" ] && [ ! -e "$HOME/.rogertabs" ]; then
    mv "$LEGACY" "$HOME/.rogertabs" && MOVED=1 && echo "  + moved $LEGACY to $HOME/.rogertabs"
fi
ROGERTABS_HOME="${ROGERTABS_HOME:-${MEET_HOME:-$HOME/.rogertabs}}"
CORE="$ROGERTABS_HOME/core"
BIN="$ROGERTABS_HOME/bin"

fail() { printf '  x %s\n      %s\n' "$1" "$2" >&2; exit 1; }

echo "ROGERTABS INSTALL"
PYTHON=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] not in ((3, 12), (3, 13)))' 2>/dev/null; then
        PYTHON="$candidate"; break
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.12 or 3.13 was not found" "Install it (macOS: brew install python@3.12) and re-run."
echo "  + $($PYTHON --version)"

mkdir -p "$ROGERTABS_HOME" "$BIN"
[ -x "$CORE/bin/python" ] || "$PYTHON" -m venv "$CORE" || fail "could not create $CORE" \
    "Debian/Ubuntu: sudo apt install python3.12-venv"
PIP="$CORE/bin/python -m pip --disable-pip-version-check install --no-input --quiet"
$PIP --require-hashes --no-deps -r "$SOURCE/requirements.lock" || fail "installing dependencies failed" "Check your connection."
$PIP --no-deps --force-reinstall "$SOURCE" || fail "installing Rogertabs failed" "See the error above."
# `rogertabs`, plus `meet` as a transition alias that says "Meet is now Rogertabs".
write_launchers() {
    mkdir -p "$1"
    for name in rogertabs meet; do
        rm -f "$1/$name"
        printf '#!/bin/sh\nexec "%s/bin/python" -Pm %s "$@"\n' "$CORE" "$name" > "$1/$name"
        chmod +x "$1/$name"
    done
}
write_launchers "$BIN"
# The old PATH entry (~/.meet/bin) keeps working after a move.
[ -z "$MOVED" ] || write_launchers "$LEGACY/bin"
echo "  + Rogertabs app in $CORE"

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "  ! add $BIN to your PATH, e.g.: echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.profile" ;;
esac

[ "${1:-}" = "--skip-setup" ] && { echo "  done. Next: rogertabs setup"; exit 0; }
exec "$BIN/rogertabs" setup
