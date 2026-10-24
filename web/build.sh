#!/usr/bin/env bash
# Builds Pumpkin's web UI: the llama.cpp chat UI at the pinned release, plus Pumpkin's pages.
# Needs git and Node.js 22+. Output goes to pumpkin/ui.
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
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=8192}"
# the app is called Pumpkin in page titles, the manifest and the home screen icon
sed -i "s/|| 'llama-ui'/|| 'Pumpkin'/" src/lib/constants/app.constants.ts
# llama.cpp ships its UI as one file so it fits inside llama-server; Pumpkin serves files, so it is split into
# pieces and a phone only loads the code for the page it opens
sed -i "s/bundleStrategy: 'single'/bundleStrategy: 'split'/" svelte.config.js
# no service worker: its offline copy could keep an old app (or an empty page) on screen after an update
sed -i "s#from 'virtual:pwa-register/svelte'#from '\$lib/pumpkin/no-sw'#" src/lib/hooks/use-pwa.svelte.ts
grep -q "lib/pumpkin/no-sw" src/lib/hooks/use-pwa.svelte.ts
# a picture the model drew (image_generate, image_edit) shows in the chat itself instead of inside a folded tool block
TB=src/lib/components/app/chat/ChatMessages/ChatMessage/ChatMessageToolCall/ChatMessageToolCallBlock.svelte
sed -i "0,/^\timport ChatMessageToolCallBlockDefault/s##\timport PumpkinToolImage from '\$lib/pumpkin/components/ToolImage.svelte';\n\timport PumpkinToolVideo from '\$lib/pumpkin/components/ToolVideo.svelte';\n&#" "$TB"
sed -i "s#^{\#if isSearchCall}#{\#if section.toolName === 'image_generate' || section.toolName === 'image_edit'}\n\t<PumpkinToolImage {isStreaming} {section} />\n{:else if section.toolName === 'image_video'}\n\t<PumpkinToolVideo {isStreaming} {section} />\n{:else if isSearchCall}#" "$TB"
grep -q "PumpkinToolImage {isStreaming}" "$TB"
grep -q "PumpkinToolVideo {isStreaming}" "$TB"
# the chat's full-screen preview of a page or drawing gets "Save to Files" and "Download"
PV=src/lib/components/app/dialogs/DialogCodePreview.svelte
sed -i "0,/^\timport XIcon/s##\timport PumpkinPreviewBar from '\$lib/pumpkin/components/PreviewBar.svelte';\n&#" "$PV"
sed -i 's#^\(\t*\)<DialogPrimitive.Content class="code-preview-content">#&\n\1\t<PumpkinPreviewBar {code} {language} />#' "$PV"
grep -q "<PumpkinPreviewBar {code} {language} />" "$PV"
grep -q "import PumpkinPreviewBar" "$PV"
# the chat's logo is a jack-o'-lantern in Halloween week, like Pumpkin's own
LG=src/lib/components/app/misc/Logo.svelte
sed -i "s#^\timport logoMark from '\$lib/assets/logo.svg?raw';#\timport plainMark from '\$lib/assets/logo.svg?raw';\n\timport jackMark from '\$lib/assets/logo-halloween.svg?raw';\n\timport { isHalloween } from '\$lib/pumpkin/season';\n\tconst logoMark = isHalloween() ? jackMark : plainMark;#" "$LG"
grep -q "isHalloween() ? jackMark : plainMark" "$LG"
npm ci --no-audit --no-fund
npm install --no-save --no-audit --no-fund qrcode-generator@1.4.4
npm run build
rm -rf "$OUT"
cp -r dist "$OUT"
echo "$TAG" > "$OUT/LLAMA_TAG"
echo "Built the UI into $OUT"
