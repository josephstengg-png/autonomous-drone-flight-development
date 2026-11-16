#!/usr/bin/env bash
# Build the release into dist/.  Needs internet ONCE, to download the dependency wheels; the bundles then install offline.
#
#   bash packaging/build_bundles.sh
#
#   dist/artex_sar-<ver>-py3-none-any.whl ..... the app (console, planner, fleet ops, assistant, demo, starter data)
#   dist/artex-<ver>-laptop-offline.tar.gz .... wheel + x86_64 dependency wheels (Python 3.10 and 3.12) + install-laptop.sh
#   dist/artex-sar_<ver>_amd64.deb ............ the same, as a Debian package: installs to /opt/artex, command artex-console
#   dist/artex-<ver>-companion.tar.gz ......... for each Jetson: the companion payload + aarch64/py3.12 wheels + install-companion.sh
#   dist/SHA256SUMS (+ SHA256SUMS.asc if a GPG key is configured: ARTEX_SIGN_KEY=<key id>)
#
# The companion fingerprint the console's version handshake expects is printed and written into the companion bundle.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
VER="$("$PY" -c 'import artex; print(artex.__version__)')"
FP="$("$PY" -m artex.common.version | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"
DIST="$ROOT/dist"; BUILD="$ROOT/build/release"
rm -rf "$BUILD"; mkdir -p "$DIST" "$BUILD"

# docs travel with the app (the assistant searches them; the operator reads them)
rm -rf artex/starter/docs; mkdir -p artex/starter/docs
cp docs/*.md artex/starter/docs/; cp README.md artex/starter/README.md

echo "== wheel"
"$PY" -m pip wheel --quiet --no-deps --wheel-dir "$DIST" "$ROOT"
WHEEL="$(ls -t "$DIST"/artex_sar-"$VER"-*.whl | head -1)"

echo "== laptop bundle (x86_64, offline)"
L="$BUILD/artex-$VER-laptop"; mkdir -p "$L/wheels"
cp "$WHEEL" "$L/wheels/"
for pyv in 310 312; do
  "$PY" -m pip download --quiet -d "$L/wheels" --only-binary=:all: --platform manylinux2014_x86_64 \
      --platform manylinux_2_28_x86_64 --python-version "$pyv" --implementation cp "pymavlink>=2.4" "PyYAML>=6" "matplotlib>=3.5"
done
# the desktop window: pywebview and its pure-Python dependencies (proxy_tools ships only as source: build its wheel here)
"$PY" -m pip download --quiet -d "$L/wheels" --only-binary=:all: --no-deps "pywebview>=5" bottle typing_extensions
"$PY" -m pip wheel --quiet --no-deps --use-pep517 -w "$L/wheels" proxy_tools
cp packaging/install-laptop.sh packaging/artex-console.desktop "$L/"
tar -C "$BUILD" -czf "$DIST/artex-$VER-laptop-offline.tar.gz" "artex-$VER-laptop"

echo "== Debian package"
D="$BUILD/deb"; mkdir -p "$D/DEBIAN" "$D/opt/artex" "$D/usr/bin" "$D/usr/share/applications"
cp -r "$L/wheels" "$L/install-laptop.sh" "$D/opt/artex/"
cat > "$D/DEBIAN/control" <<EOF
Package: artex-sar
Version: $VER
Architecture: amd64
Maintainer: ARTEX
Depends: python3 (>= 3.10), python3-venv, python3-gi, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0
Description: ARTEX search-and-rescue ground station (console, mission planner, fleet tools, assistant)
EOF
cat > "$D/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
/opt/artex/install-laptop.sh --prefix /opt/artex --no-desktop >/dev/null
EOF
chmod 755 "$D/DEBIAN/postinst"
cat > "$D/usr/bin/artex-console" <<'EOF'
#!/bin/sh
exec /opt/artex/venv/bin/artex-console "$@"
EOF
chmod 755 "$D/usr/bin/artex-console"
cp packaging/artex-console.desktop "$D/usr/share/applications/"
fakeroot dpkg-deb --build "$D" "$DIST/artex-sar_${VER}_amd64.deb" >/dev/null

echo "== companion bundle (aarch64, Python 3.12, offline)"
C="$BUILD/artex-$VER-companion"; mkdir -p "$C/wheels"
"$PY" - "$C" <<'PYEOF'
import shutil, sys
from pathlib import Path
from artex.common.layout import COMPANION_PAYLOAD, ROOT
dst = Path(sys.argv[1]) / "app"
for rel in COMPANION_PAYLOAD:
    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / rel, dst / rel)
PYEOF
"$PY" -m pip download --quiet -d "$C/wheels" --only-binary=:all: --platform manylinux2014_aarch64 --platform manylinux_2_28_aarch64 \
    --python-version 312 --implementation cp "pymavlink>=2.4" "PyYAML>=6"
cp packaging/install-companion.sh packaging/jetson/artex-fallback.service "$C/"
echo "$FP" > "$C/FINGERPRINT"
tar -C "$BUILD" -czf "$DIST/artex-$VER-companion.tar.gz" "artex-$VER-companion"

echo "== checksums"
( cd "$DIST" && sha256sum artex_sar-"$VER"-*.whl artex-"$VER"-*.tar.gz artex-sar_"$VER"_amd64.deb > SHA256SUMS )
if [ -n "${ARTEX_SIGN_KEY:-}" ]; then
  gpg --batch --yes --local-user "$ARTEX_SIGN_KEY" --armor --detach-sign "$DIST/SHA256SUMS"
  echo "signed SHA256SUMS with $ARTEX_SIGN_KEY"
else
  echo "not signed (set ARTEX_SIGN_KEY=<gpg key id> to sign SHA256SUMS)"
fi
rm -rf artex/starter/docs artex/starter/README.md
echo "built $VER; companion fingerprint $FP"
ls -la "$DIST"
