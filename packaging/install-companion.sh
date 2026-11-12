#!/usr/bin/env bash
# Install the ARTEX companion payload on a Jetson, offline, from this bundle. Run ON the Jetson:
#   ./install-companion.sh                 # app into ~/artex_app, Python packages for this user
#   ./install-companion.sh --fallback      # also install the stop-only fallback watchdog unit (edit it first; needs sudo)
# It does NOT touch the radio relay service (elrs-link). To move the relay onto this app:
#   sudo bash ~/artex_app/artex/fleetops/elrs_service.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/artex_app"
mkdir -p "$DEST"
cp -r "$HERE/app/." "$DEST/"
# Ubuntu 24.04 marks the system Python as externally managed; the existing agent installs already use --user this way
python3 -m pip install --quiet --user --break-system-packages --no-index --find-links "$HERE/wheels" pymavlink PyYAML
( cd "$DEST" && python3 -m artex.common.version )
echo "expected fingerprint: $(cat "$HERE/FINGERPRINT")"
if [ "${1:-}" = "--fallback" ]; then
  sudo cp "$HERE/artex-fallback.service" /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now artex-fallback
fi
echo "Companion installed in $DEST."
