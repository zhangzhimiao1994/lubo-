#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BUILD_ROOT="$REPO_ROOT/.android-build"
PROJECT_DIR="$BUILD_ROOT/project"
SOURCE_DIR="$PROJECT_DIR/appsource"
BIN_DIR="$PROJECT_DIR/bin"
EXPECTED_PROJECT_DIR="$REPO_ROOT/.android-build/project"
EXPECTED_SOURCE_DIR="$REPO_ROOT/.android-build/project/appsource"
EXPECTED_BIN_DIR="$REPO_ROOT/.android-build/project/bin"
BUILDOZER_BIN="${BUILDOZER:-buildozer}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Android builds require Linux. Use GitHub Actions or WSL2." >&2
    exit 1
fi
if ! command -v "$BUILDOZER_BIN" >/dev/null 2>&1; then
    echo "Buildozer was not found. Add it to PATH or set BUILDOZER." >&2
    exit 1
fi
if [[ "$PROJECT_DIR" != "$EXPECTED_PROJECT_DIR" ||
    "$SOURCE_DIR" != "$EXPECTED_SOURCE_DIR" ||
    "$BIN_DIR" != "$EXPECTED_BIN_DIR" ||
    -L "$BUILD_ROOT" || -L "$PROJECT_DIR" || -L "$SOURCE_DIR" || -L "$BIN_DIR" ]]; then
    echo "Refusing to refresh unexpected or symlinked Android build paths." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR"
if [[ -e "$BIN_DIR" ]]; then
    rm -rf -- "$BIN_DIR"
fi
mkdir -p "$BIN_DIR"
if [[ -e "$SOURCE_DIR" ]]; then
    rm -rf -- "$SOURCE_DIR"
fi
mkdir -p "$SOURCE_DIR/service"

cp -- "$REPO_ROOT/android/main.py" "$SOURCE_DIR/main.py"
cp -- "$REPO_ROOT/android/service/recorder_service.py" "$SOURCE_DIR/service/recorder_service.py"
cp -R -- "$REPO_ROOT/android/java" "$SOURCE_DIR/java"
cp -R -- "$REPO_ROOT/lubo" "$SOURCE_DIR/lubo"

python3 "$REPO_ROOT/scripts/prepare_packaged_config.py" \
    --source "$REPO_ROOT/config" \
    --output "$SOURCE_DIR/config"
cp -- "$REPO_ROOT/android/buildozer.spec" "$PROJECT_DIR/buildozer.spec"
cp -- "$REPO_ROOT/android/p4a_hook.py" "$PROJECT_DIR/p4a_hook.py"

(
    cd "$PROJECT_DIR"
    "$BUILDOZER_BIN" android debug
)

APK_PATHS=()
mapfile -d '' APK_PATHS < <(
    find "$BIN_DIR" -maxdepth 1 -type f -name '*.apk' -print0
)
if [[ ${#APK_PATHS[@]} -ne 1 ]]; then
    echo "Expected exactly one APK from the current build; found ${#APK_PATHS[@]}." >&2
    exit 1
fi
APK_PATH="${APK_PATHS[0]}"

DIST_DIR="$REPO_ROOT/dist/android"
OUTPUT_APK="$REPO_ROOT/dist/android/Lubo-android-debug.apk"
mkdir -p "$DIST_DIR"
cp -- "$APK_PATH" "$OUTPUT_APK"
printf 'Android build complete: %s\n' "$OUTPUT_APK"
