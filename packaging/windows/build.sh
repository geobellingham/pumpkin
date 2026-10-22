#!/usr/bin/env bash
# Builds pumpkin-<version>-windows-x64.zip: Pumpkin with its own Python (the official embeddable build from
# python.org, checked against python.org's Sigstore signature), so Windows needs nothing else installed.
#   packaging/windows/build.sh <version> [out-dir]      needs: curl, unzip, zip, python3 (+ pip for sigstore)
# The web UI must be built first (web/build.sh puts it in pumpkin/ui).
set -euo pipefail
VERSION=${1:?version}
OUT=${2:-dist}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PYMINOR=${PYMINOR:-3.13}
# who signs each Python release line (https://www.python.org/downloads/metadata/sigstore/)
SIGNER=${PYSIGNER:-thomas@python.org}
ISSUER=${PYISSUER:-https://accounts.google.com}
[ -f "$ROOT/pumpkin/ui/index.html" ] || { echo "Build the web UI first: web/build.sh"; exit 1; }

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# the newest $PYMINOR.x that has an embeddable build
PYVER=${PYVER:-}
if [ -z "$PYVER" ]; then
  for n in $(seq 40 -1 0); do
    if curl -sfI "https://www.python.org/ftp/python/$PYMINOR.$n/python-$PYMINOR.$n-embed-amd64.zip" >/dev/null; then
      PYVER="$PYMINOR.$n"; break
    fi
  done
fi
[ -n "$PYVER" ] || { echo "No embeddable Python $PYMINOR found"; exit 1; }
EMBED="python-$PYVER-embed-amd64.zip"
echo "Python $PYVER"
curl -sSfL -o "$WORK/$EMBED" "https://www.python.org/ftp/python/$PYVER/$EMBED"
curl -sSfL -o "$WORK/$EMBED.sigstore" "https://www.python.org/ftp/python/$PYVER/$EMBED.sigstore"
if [ -z "${SKIP_SIGSTORE:-}" ]; then
  python3 -m sigstore --version >/dev/null 2>&1 || python3 -m pip install --quiet "sigstore>=3,<5"
  python3 -m sigstore verify identity --bundle "$WORK/$EMBED.sigstore" \
    --cert-identity "$SIGNER" --cert-oidc-issuer "$ISSUER" "$WORK/$EMBED"
fi

PKG="$WORK/Pumpkin"
mkdir -p "$PKG/python" "$PKG/app" "$PKG/bin"
unzip -q "$WORK/$EMBED" -d "$PKG/python"
# the embeddable build reads its search path from pythonXY._pth: add Pumpkin's folder to it
PTH=$(ls "$PKG/python"/python3*._pth)
ZIPNAME=$(basename "$PTH" ._pth).zip
printf '%s\r\n.\r\n..\\app\r\n' "$ZIPNAME" > "$PTH"

cp -r "$ROOT/pumpkin" "$PKG/app/"
rm -rf "$PKG/app/pumpkin/mac"
find "$PKG/app" -name __pycache__ -prune -exec rm -rf {} +
sed -i.bak "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$PKG/app/pumpkin/__init__.py" && rm "$PKG/app/pumpkin/__init__.py.bak"
grep -q "__version__ = \"$VERSION\"" "$PKG/app/pumpkin/__init__.py"
cp "$ROOT/pumpkin/assets/pumpkin.ico" "$PKG/pumpkin.ico"
cp "$ROOT/uninstall.ps1" "$ROOT/LICENSE" "$ROOT/NOTICE" "$PKG/"
printf '@echo off\r\n"%%~dp0..\\python\\python.exe" -X utf8 -m pumpkin %%*\r\n' > "$PKG/bin/pumpkin.cmd"
echo "$PYVER" > "$PKG/python/PUMPKIN_PYTHON_VERSION"

mkdir -p "$OUT"
OUTZIP="$(cd "$OUT" && pwd)/pumpkin-$VERSION-windows-x64.zip"
rm -f "$OUTZIP"
(cd "$WORK" && zip -qr -9 "$OUTZIP" Pumpkin)
ls -la "$OUTZIP"
