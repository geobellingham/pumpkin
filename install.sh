#!/usr/bin/env bash
# Pumpkin installer. Run from a release folder or a git checkout, or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/geobellingham/pumpkin/main/install.sh | bash
# Options: --service  also start Pumpkin at login     --no-open  do not open it afterwards
set -euo pipefail

REPO="${PUMPKIN_REPO:-geobellingham/pumpkin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pumpkin/app"
BIN_DIR="$HOME/.local/bin"
SERVICE=0; OPEN=1
for a in "$@"; do
  case "$a" in
    --service) SERVICE=1 ;;
    --no-open) OPEN=0 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done

say() { printf '\033[1;33m🎃\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "Pumpkin 0.1 runs on Linux. macOS and Windows are planned."
command -v python3 >/dev/null || die "Python 3.10 or newer is needed: sudo apt install python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is needed."

# where to install from
SRC=""
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/pumpkin/__init__.py" ]; then
  SRC="$HERE"
else
  say "Downloading the latest Pumpkin release"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/##')"
  [ -n "$TAG" ] || die "Could not find a release of $REPO."
  curl -fsSL "https://github.com/$REPO/releases/download/$TAG/pumpkin-${TAG#v}.tar.gz" | tar -xz -C "$TMP"
  SRC="$(find "$TMP" -maxdepth 2 -name install.sh -printf '%h\n' | head -1)"
fi

if [ ! -f "$SRC/pumpkin/ui/index.html" ]; then
  if command -v node >/dev/null && command -v git >/dev/null; then
    say "Building the web interface (first time only, takes a few minutes)"
    "$SRC/web/build.sh"
  else
    die "This checkout has no built web interface. Install Node.js 20+ and git, or use a release download."
  fi
fi

say "Installing to $APP_DIR"
if command -v pumpkin >/dev/null && pumpkin stop >/dev/null 2>&1; then :; fi
rm -rf "$APP_DIR.new"; mkdir -p "$APP_DIR.new"
cp -r "$SRC/pumpkin" "$APP_DIR.new/"
cp "$SRC/LICENSE" "$SRC/README.md" "$SRC/uninstall.sh" "$APP_DIR.new/" 2>/dev/null || true
rm -rf "$APP_DIR"; mv "$APP_DIR.new" "$APP_DIR"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/pumpkin" <<WRAP
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m pumpkin "\$@"
WRAP
chmod +x "$BIN_DIR/pumpkin"

ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$ICON_DIR" "$APPS_DIR"
cp "$APP_DIR/pumpkin/assets/pumpkin.svg" "$ICON_DIR/pumpkin.svg"
sed "s#^Exec=pumpkin#Exec=$BIN_DIR/pumpkin#" "$SRC/packaging/pumpkin.desktop" > "$APPS_DIR/pumpkin.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "Add $BIN_DIR to your PATH to use the pumpkin command (most desktops do this after you log in again)." ;; esac

if ! python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' >/dev/null 2>&1; then
  say "Optional: for Pumpkin's own window instead of a browser tab, run: sudo apt install python3-gi gir1.2-webkit2-4.1"
fi
if ! command -v docker >/dev/null; then
  say "Optional: agent tools run in a Docker sandbox. Install it with: sudo apt install docker.io && sudo usermod -aG docker \$USER"
fi

if [ "$SERVICE" = 1 ]; then
  "$BIN_DIR/pumpkin" service install
fi
say "Pumpkin is installed. Open it from your app menu or run: pumpkin"
if [ "$OPEN" = 1 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
  nohup "$BIN_DIR/pumpkin" >/dev/null 2>&1 &
fi
