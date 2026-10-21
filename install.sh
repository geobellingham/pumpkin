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

OS="$(uname -s)"
case "$OS" in
  Linux|Darwin) ;;
  *) die "Pumpkin runs on Linux and macOS. Windows is planned." ;;
esac
MAC=0; [ "$OS" = "Darwin" ] && MAC=1

# the newest Python 3.10+ there is. A Mac's built-in python3 is 3.9, so Homebrew's is preferred there.
PY=""
if [ "$MAC" = 1 ]; then
  CANDS="python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3"
else
  CANDS="python3 python3.14 python3.13 python3.12 python3.11 python3.10"   # the system one has GTK for the window
fi
for c in $CANDS; do
  p="$(command -v "$c" 2>/dev/null || true)"
  if [ -n "$p" ] && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  if [ "$MAC" = 1 ]; then
    die "Pumpkin needs Python 3.10 or newer, and the one built into macOS is older. Install it with Homebrew (brew install python) or from python.org, then run this again."
  fi
  die "Python 3.10 or newer is needed: sudo apt install python3"
fi

# where to install from
SRC=""
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/pumpkin/__init__.py" ]; then
  SRC="$HERE"
else
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  TAG="${PUMPKIN_TAG:-}"
  if [ -z "$TAG" ]; then
    TAG="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" | sed 's#.*/tag/##')"
  fi
  [ -n "$TAG" ] || die "Could not find a release of $REPO."
  FILE="pumpkin-${TAG#v}.tar.gz"
  if [ -z "${PUMPKIN_TAG:-}" ] && ! curl -fsSL -r 0-0 -o /dev/null "https://github.com/$REPO/releases/download/$TAG/$FILE" 2>/dev/null; then
    # a release that was just published has no download for the first few minutes, while GitHub builds it
    PREV="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases?per_page=10" \
      | "$PY" -c 'import json,sys; print(next((r["tag_name"] for r in json.load(sys.stdin) if r["tag_name"] != sys.argv[1] and not r.get("draft") and not r.get("prerelease") and any(a["name"] == "pumpkin-" + r["tag_name"].lstrip("v") + ".tar.gz" for a in r.get("assets", []))), ""))' "$TAG" \
      2>/dev/null || true)"
    [ -n "$PREV" ] || die "Pumpkin $TAG is still being built on GitHub. Try again in a couple of minutes."
    say "Pumpkin $TAG is still being built on GitHub, so installing $PREV. Run this again later to get $TAG."
    TAG="$PREV"; FILE="pumpkin-${TAG#v}.tar.gz"
  fi
  say "Downloading Pumpkin $TAG"
  curl -fsSL -o "$TMP/$FILE" "https://github.com/$REPO/releases/download/$TAG/$FILE" || die "Download failed."
  # check the file against the SHA-256 GitHub publishes for it
  WANT="$(curl -fsSL -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$REPO/releases/tags/$TAG" \
    | "$PY" -c 'import json,sys; f=sys.argv[1]; print(next((a.get("digest") or "" for a in json.load(sys.stdin).get("assets", []) if a.get("name") == f), ""))' "$FILE" \
    2>/dev/null || true)"
  GOT="sha256:$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/$FILE")"
  if [ -n "$WANT" ]; then
    [ "$WANT" = "$GOT" ] || die "The download does not match its published checksum. Nothing was installed."
    say "Checksum verified"
  elif [ -z "${PUMPKIN_SKIP_VERIFY:-}" ]; then
    die "Could not get the checksum from GitHub to verify the download. Try again in a minute (or set PUMPKIN_SKIP_VERIFY=1)."
  fi
  tar -xzf "$TMP/$FILE" -C "$TMP"
  SRC="$(dirname "$(find "$TMP" -maxdepth 2 -name install.sh | head -1)")"
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
# stop the running Pumpkin, found by its full path (an app started from the menu or the Mac app has no
# ~/.local/bin on its PATH). When this runs from Pumpkin's own login service (the Update button), stopping the
# service would also stop this script, so the service is restarted at the very end instead.
UNDER_SERVICE=""
if grep -qs "pumpkin.service" /proc/self/cgroup 2>/dev/null; then UNDER_SERVICE=systemd
elif [ "${XPC_SERVICE_NAME:-}" = "app.pumpkin" ]; then UNDER_SERVICE=launchd; fi
OLD="$BIN_DIR/pumpkin"; [ -x "$OLD" ] || OLD="$(command -v pumpkin 2>/dev/null || true)"
if [ -z "$UNDER_SERVICE" ]; then
  [ -n "$OLD" ] && { "$OLD" stop >/dev/null 2>&1 || true; }
  # make sure it really stopped: older versions could miss it (a proxy in the environment, or no PATH)
  CONF="${XDG_CONFIG_HOME:-$HOME/.config}/pumpkin"
  PORT="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("port", 8090))' "$CONF/settings.json" 2>/dev/null || echo 8090)"
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    curl -s --noproxy '*' -o /dev/null -m 2 "http://127.0.0.1:$PORT/" 2>/dev/null || break
    if [ -f "$CONF/token" ]; then
      curl -s --noproxy '*' -o /dev/null -m 3 -X POST -H "Authorization: Bearer $(cat "$CONF/token")" -H "X-Pumpkin: 1" \
        -H "Content-Type: application/json" -d '{}' "http://127.0.0.1:$PORT/pumpkin/api/quit" 2>/dev/null || true
    fi
    sleep 0.5
  done
fi
rm -rf "$APP_DIR.new"; mkdir -p "$APP_DIR.new"
cp -r "$SRC/pumpkin" "$APP_DIR.new/"
cp "$SRC/LICENSE" "$SRC/NOTICE" "$SRC/README.md" "$SRC/uninstall.sh" "$APP_DIR.new/" 2>/dev/null || true
rm -rf "$APP_DIR"; mv "$APP_DIR.new" "$APP_DIR"
find "$APP_DIR" -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/pumpkin" <<WRAP
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m pumpkin "\$@"
WRAP
chmod +x "$BIN_DIR/pumpkin"

if [ "$MAC" = 1 ]; then
  # Pumpkin.app in ~/Applications, so it is in Launchpad, Spotlight and the Dock. Releases carry a small native
  # window (pumpkin/mac/Pumpkin, built on GitHub's Macs); a source checkout without it opens the browser instead.
  APP="$HOME/Applications/Pumpkin.app"
  rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cp "$APP_DIR/pumpkin/assets/pumpkin.icns" "$APP/Contents/Resources/pumpkin.icns" 2>/dev/null || true
  if [ -f "$APP_DIR/pumpkin/mac/Pumpkin" ]; then
    cp "$APP_DIR/pumpkin/mac/Pumpkin" "$APP/Contents/MacOS/Pumpkin"
    BACKGROUND=""
  else
    printf '#!/bin/sh\nexec "%s" open --browser\n' "$BIN_DIR/pumpkin" > "$APP/Contents/MacOS/Pumpkin"
    BACKGROUND="<key>LSUIElement</key><true/>"
  fi
  chmod +x "$APP/Contents/MacOS/Pumpkin"
  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Pumpkin</string>
  <key>CFBundleDisplayName</key><string>Pumpkin</string>
  <key>CFBundleIdentifier</key><string>app.pumpkin.window</string>
  <key>CFBundleExecutable</key><string>Pumpkin</string>
  <key>CFBundleIconFile</key><string>pumpkin</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$("$PY" -c "import sys; sys.path.insert(0, '$APP_DIR'); import pumpkin; print(pumpkin.__version__)")</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Pumpkin uses the microphone for voice chat.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>Pumpkin turns what you say into text for voice chat.</string>
  <key>PumpkinCommand</key><string>$BIN_DIR/pumpkin</string>
  $BACKGROUND
</dict></plist>
PLIST
  # sign it for this Mac only (no Apple account involved), which Apple Silicon needs to run an app
  command -v codesign >/dev/null && codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
  touch "$APP"
else
  ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$ICON_DIR" "$APPS_DIR"
  cp "$APP_DIR/pumpkin/assets/pumpkin.svg" "$ICON_DIR/pumpkin.svg"
  sed "s#^Exec=pumpkin#Exec=$BIN_DIR/pumpkin#" "$SRC/packaging/pumpkin.desktop" > "$APPS_DIR/pumpkin.desktop"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    if [ "$MAC" = 1 ]; then
      say "To use the pumpkin command in Terminal, run: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zprofile"
    else
      say "Add $BIN_DIR to your PATH to use the pumpkin command (most desktops do this after you log in again)."
    fi ;;
esac

if [ "$MAC" = 1 ]; then
  if ! command -v docker >/dev/null; then
    say "Optional: agent tools run in a Docker sandbox. Install Docker Desktop (brew install --cask docker) and open it once."
  fi
else
  if ! "$PY" -c 'import gi; gi.require_version("WebKit2", "4.1")' >/dev/null 2>&1; then
    say "Optional: for Pumpkin's own window instead of a browser tab, run: sudo apt install python3-gi gir1.2-webkit2-4.1"
  fi
  if ! command -v docker >/dev/null; then
    say "Optional: agent tools run in a Docker sandbox. Install it with: sudo apt install docker.io && sudo usermod -aG docker \$USER"
  fi
fi

if [ "$SERVICE" = 1 ]; then
  "$BIN_DIR/pumpkin" service install
fi
if [ "$MAC" = 1 ]; then
  say "Pumpkin is installed. Open it from Launchpad or Spotlight, or run: pumpkin"
else
  say "Pumpkin is installed. Open it from your app menu or run: pumpkin"
fi
case "$UNDER_SERVICE" in
  systemd) systemctl --user --no-block restart pumpkin.service ;;
  launchd) launchctl kickstart -k "gui/$(id -u)/app.pumpkin" >/dev/null 2>&1 || true ;;
esac
if [ "$OPEN" = 1 ] && [ "$MAC" = 1 ]; then
  open "$HOME/Applications/Pumpkin.app"
elif [ "$OPEN" = 1 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
  nohup "$BIN_DIR/pumpkin" >/dev/null 2>&1 &
fi
