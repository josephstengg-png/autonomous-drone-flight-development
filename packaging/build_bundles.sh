#!/usr/bin/env bash
# Build the release bundles into dist/. Offline-safe: no network is needed for what this script does today.
#
#   bash packaging/build_bundles.sh
#
#   dist/artex-<ver>-console.whl ............ the whole app as a wheel (laptop: console, planner, fleet ops, assistant)
#   dist/artex-<ver>-companion.tar.gz ....... exactly artex.common.layout.COMPANION_PAYLOAD, for each Jetson
#   dist/artex-<ver>-companion.sha256 ....... the fingerprint the console's version handshake expects
#
# Not done yet (Phase A leaves them as steps): the .deb wrappers, the offline dependency wheels for aarch64
# (pymavlink, PyYAML for Python 3.12 on the Jetsons, which have no internet), signing, and the model bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
VER="$("$PY" -c 'import artex; print(artex.__version__)')"
FP="$("$PY" -m artex.common.version | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
mkdir -p dist
"$PY" -m pip wheel --quiet --no-deps --wheel-dir dist "$ROOT"
"$PY" - "$VER" <<'PYEOF'
import sys, tarfile
from artex.common.layout import COMPANION_PAYLOAD, ROOT
ver = sys.argv[1]
with tarfile.open(ROOT / "dist" / f"artex-{ver}-companion.tar.gz", "w:gz") as t:
    for rel in COMPANION_PAYLOAD:
        t.add(ROOT / rel, arcname=rel)
PYEOF
echo "$FP  artex-$VER-companion" > "dist/artex-$VER-companion.sha256"
echo "built version $VER, companion fingerprint $FP:"
ls -1 dist
