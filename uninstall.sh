#!/usr/bin/env bash
# Removes Pumpkin. Your models, reports and settings stay unless you add --purge.
set -u
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
# the installed command first: a desktop session often has no ~/.local/bin on its PATH
P="$HOME/.local/bin/pumpkin"; [ -x "$P" ] || P="$(command -v pumpkin 2>/dev/null || true)"
if [ -n "$P" ]; then
  "$P" service uninstall >/dev/null 2>&1
  "$P" stop >/dev/null 2>&1
fi
rm -rf "$DATA/pumpkin/app" "$HOME/.local/bin/pumpkin" "$DATA/applications/pumpkin.desktop" \
       "$DATA/icons/hicolor/scalable/apps/pumpkin.svg" "$HOME/Applications/Pumpkin.app"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/pumpkin" "$DATA/pumpkin" "${XDG_STATE_HOME:-$HOME/.local/state}/pumpkin"
  echo "Removed Pumpkin and its settings. Models and reports in ~/Pumpkin were kept; delete that folder yourself if you want."
else
  echo "Removed Pumpkin. Settings, models and reports were kept (use --purge to remove settings too)."
fi
