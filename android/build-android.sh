#!/usr/bin/env bash
# Builds Pumpkin for Android: llama-server for phones (arm64) and the emulator (x86_64) with the NDK, then the
# APK with Gradle. Needs: the Android SDK and NDK, Java 17, Gradle, Python 3.12, and the web UI already built
# into pumpkin/ui (web/build.sh). Output: android/app/build/outputs/apk/{release,debug}/*.apk
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
NDK=${ANDROID_NDK_HOME:-${ANDROID_NDK_LATEST_HOME:-}}
[ -d "$NDK" ] || { echo "Set ANDROID_NDK_HOME to the NDK"; exit 1; }
[ -f "$ROOT/pumpkin/ui/index.html" ] || { echo "Build the web UI first: web/build.sh"; exit 1; }
TAG=$(cat "$ROOT/web/LLAMA_TAG")
VERSION=${PUMPKIN_VERSION:-$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import pumpkin; print(pumpkin.__version__)")}
WORK=${WORK:-$HERE/.build}
JOBS=$(nproc 2>/dev/null || echo 4)

# ---- llama-server, the same llama.cpp release the UI is built from
if [ ! -d "$WORK/llama.cpp/.git" ]; then
  mkdir -p "$WORK"
  git clone --quiet --depth 1 --branch "$TAG" https://github.com/ggml-org/llama.cpp "$WORK/llama.cpp"
fi
# arm64 gets two builds: a fast one for phones with the dot-product instructions (about 2018 and newer) and a
# basic one that runs on every 64-bit phone, such as the Galaxy S8. Pumpkin picks at startup.
for variant in arm64-v8a:libllama_server.so:fast arm64-v8a:libllama_server_basic.so:basic x86_64:libllama_server.so:plain; do
  IFS=: read -r abi name kind <<<"$variant"
  out="$HERE/app/src/main/jniLibs/$abi/$name"
  if [ -f "$out" ] && [ -z "${REBUILD:-}" ]; then continue; fi
  extra=()
  [ "$kind" = fast ] && extra+=(-DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16)
  [ "$kind" = basic ] && extra+=(-DGGML_CPU_ARM_ARCH=armv8-a)
  cmake -S "$WORK/llama.cpp" -B "$WORK/build-$abi-$kind" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI="$abi" \
    -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_static -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON \
    -DLLAMA_BUILD_NUMBER="${TAG#b}" "${extra[@]}"          # a shallow clone cannot count its own commits
  cmake --build "$WORK/build-$abi-$kind" --target llama-server -j "$JOBS"
  mkdir -p "$(dirname "$out")"
  cp "$WORK/build-$abi-$kind/bin/llama-server" "$out"
  "$NDK"/toolchains/llvm/prebuilt/*/bin/llvm-strip "$out"
  ls -la "$out"
done

# ---- Pumpkin's Python package with its web UI
rm -rf "$HERE/app/src/main/python"
mkdir -p "$HERE/app/src/main/python"
cp -r "$ROOT/pumpkin" "$HERE/app/src/main/python/"
find "$HERE/app/src/main/python" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$HERE/app/src/main/python/pumpkin/mac"

# ---- the APK
cd "$HERE"
gradle --no-daemon -PpumpkinVersion="$VERSION" assembleRelease assembleDebug
ls -la app/build/outputs/apk/*/
