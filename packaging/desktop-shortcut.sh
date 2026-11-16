#!/usr/bin/env bash
# Put "ARTEX Ground Station" (and a Demo one) on the desktop and in the applications menu: click and it opens.
#
#   ./desktop-shortcut.sh /path/to/artex-console              # desktop icons + menu entries
#   ./desktop-shortcut.sh /path/to/artex-console --menu-only  # menu entries only
#   ./desktop-shortcut.sh /path/to/artex-console --remove     # take them away again
#
# Called by install-laptop.sh; also works for a development checkout (point it at .venv/bin/artex-console).
# GNOME's desktop icons launch a shortcut only when it is executable and marked trusted; this does both.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXE="${1:?usage: desktop-shortcut.sh /path/to/artex-console [--menu-only|--remove]}"
MODE="${2:-}"
EXE="$(cd "$(dirname "$EXE")" && pwd)/$(basename "$EXE")"
[ -x "$EXE" ] || { echo "not an executable: $EXE"; exit 1; }

DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
APPS="$HOME/.local/share/applications"
ICONDIR="$HOME/.local/share/icons/hicolor/scalable/apps"
NAMES=(artex-ground-station artex-ground-station-demo)

if [ "$MODE" = "--remove" ]; then
  for n in "${NAMES[@]}"; do rm -f "$DESK/$n.desktop" "$APPS/$n.desktop"; done
  echo "removed the ARTEX shortcuts"; exit 0
fi

mkdir -p "$APPS" "$ICONDIR"
ICON=artex-console
if [ -f "$HERE/artex-console.svg" ]; then
  cp "$HERE/artex-console.svg" "$ICONDIR/artex-console.svg"
  ICON="$ICONDIR/artex-console.svg"          # absolute: works before the icon cache refreshes
fi

write() {   # write <file> <name> <args> <comment>
  cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Comment=$4
Exec="$EXE"${3:+ $3}
Icon=$ICON
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
  chmod +x "$1"
}

LIVE_COMMENT="Search-and-rescue mission planning and fleet console (live aircraft)"
DEMO_COMMENT="The same console with simulated aircraft: nothing is contacted"
write "$APPS/${NAMES[0]}.desktop" "ARTEX Ground Station" "" "$LIVE_COMMENT"
write "$APPS/${NAMES[1]}.desktop" "ARTEX Ground Station (Demo)" "--demo" "$DEMO_COMMENT"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true

if [ "$MODE" != "--menu-only" ] && [ -d "$DESK" ]; then
  for n in "${NAMES[@]}"; do
    cp "$APPS/$n.desktop" "$DESK/$n.desktop"
    chmod +x "$DESK/$n.desktop"
    gio set "$DESK/$n.desktop" metadata::trusted true 2>/dev/null || true   # GNOME: allow launching on click
  done
  echo "desktop: $DESK/${NAMES[0]}.desktop and the Demo one"
fi
echo "menu:    ARTEX Ground Station, ARTEX Ground Station (Demo)  -> $EXE"
