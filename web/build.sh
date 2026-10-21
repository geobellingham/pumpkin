#!/usr/bin/env bash
# Builds Pumpkin's web UI: the llama.cpp chat UI at the pinned release, plus Pumpkin's pages.
# Needs git and Node.js 20+. Output goes to pumpkin/ui.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TAG=${LLAMA_TAG:-$(cat "$HERE/LLAMA_TAG")}
WORK=${WORK:-$HERE/.build}
OUT=$HERE/../pumpkin/ui

if [ ! -d "$WORK/llama.cpp/.git" ]; then
  rm -rf "$WORK"; mkdir -p "$WORK"
  git clone --quiet --depth 1 --branch "$TAG" --filter=blob:none --sparse https://github.com/ggml-org/llama.cpp "$WORK/llama.cpp"
  git -C "$WORK/llama.cpp" sparse-checkout set tools/ui
else
  git -C "$WORK/llama.cpp" fetch --quiet --depth 1 origin tag "$TAG"
  git -C "$WORK/llama.cpp" checkout --quiet --force "$TAG"
  git -C "$WORK/llama.cpp" clean -fdq tools/ui/src
fi

UI=$WORK/llama.cpp/tools/ui
echo "llama.cpp UI at $TAG"
git -C "$WORK/llama.cpp" apply "$HERE/llama-ui.patch"
cp -r "$HERE/src/." "$UI/src/"
cd "$UI"
npm ci --no-audit --no-fund
npm install --no-save --no-audit --no-fund qrcode-generator@1.4.4
npm run build
rm -rf "$OUT"
cp -r dist "$OUT"
echo "$TAG" > "$OUT/LLAMA_TAG"
echo "Built the UI into $OUT"
