#!/usr/bin/env bash
# Builds Pumpkin's Mac window for Apple Silicon and Intel into one file: pumpkin/mac/Pumpkin. Needs Xcode's swiftc.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/../../pumpkin/mac"
TMP=$(mktemp -d)
mkdir -p "$OUT"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos12" -framework AppKit -framework WebKit "$HERE/Pumpkin.swift" -o "$TMP/Pumpkin-$arch"
done
lipo -create "$TMP/Pumpkin-arm64" "$TMP/Pumpkin-x86_64" -output "$OUT/Pumpkin"
codesign --force --sign - "$OUT/Pumpkin"
"$OUT/Pumpkin" --check
lipo -info "$OUT/Pumpkin"
