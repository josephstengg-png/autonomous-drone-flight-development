#!/usr/bin/env bash
# Install the ARTEX console on a ground-station laptop, offline, from the wheels next to this script.
#   ./install-laptop.sh                       # into ~/.local/share/artex-app, launcher in ~/.local/bin,
#                                             # icons on the desktop and in the applications menu (--no-desktop: none)
#   ./install-laptop.sh --prefix /opt/artex   # system-wide (the .deb does this)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="$HOME/.local/share/artex-app"; DESKTOP=1; LINK=1
while [ $# -gt 0 ]; do case "$1" in --prefix) PREFIX="$2"; shift 2;; --no-desktop) DESKTOP=0; shift;; --no-link) LINK=0; DESKTOP=0; shift;; *) echo "unknown $1"; exit 2;; esac; done
command -v python3 >/dev/null || { echo "python3 is needed (3.10 or newer)"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "python3 >= 3.10 is needed"; exit 1; }
mkdir -p "$PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --no-index --find-links "$HERE/wheels" artex-sar
if [ "$PREFIX" != "/opt/artex" ] && [ "$LINK" = 1 ]; then
  mkdir -p "$HOME/.local/bin"
  ln -sf "$PREFIX/venv/bin/artex-console" "$HOME/.local/bin/artex-console"
  if [ "$DESKTOP" = 1 ] && [ -x "$HERE/desktop-shortcut.sh" ]; then
    "$HERE/desktop-shortcut.sh" "$PREFIX/venv/bin/artex-console"      # click-to-run icons: live and demo
  elif [ "$DESKTOP" = 1 ] && [ -f "$HERE/artex-console.desktop" ]; then
    mkdir -p "$HOME/.local/share/applications"; cp "$HERE/artex-console.desktop" "$HOME/.local/share/applications/"
  fi
fi
echo "Installed $("$PREFIX/venv/bin/python" -c 'import artex; print(artex.__version__)') into $PREFIX."
echo "Start:  artex-console        (opens the desktop app; first run creates ~/.local/share/artex for your data; --serve for a browser)"
echo "Demo:   artex-console --demo"
