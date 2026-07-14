#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BUILD_ROOT="$REPO_ROOT/.android-build"
PROJECT_DIR="$BUILD_ROOT/project"
SOURCE_DIR="$PROJECT_DIR/appsource"
EXPECTED_SOURCE_DIR="$REPO_ROOT/.android-build/project/appsource"
BUILDOZER_BIN="${BUILDOZER:-buildozer}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Android builds require Linux. Use GitHub Actions or WSL2." >&2
    exit 1
fi
if ! command -v "$BUILDOZER_BIN" >/dev/null 2>&1; then
    echo "Buildozer was not found. Install it in a virtual environment or set BUILDOZER." >&2
    exit 1
fi
if [[ "$SOURCE_DIR" != "$EXPECTED_SOURCE_DIR" || -L "$BUILD_ROOT" || -L "$PROJECT_DIR" || -L "$SOURCE_DIR" ]]; then
    echo "Refusing to refresh an unexpected or symlinked Android staging path: $SOURCE_DIR" >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR"
if [[ -e "$SOURCE_DIR" ]]; then
    rm -rf -- "$SOURCE_DIR"
fi
mkdir -p "$SOURCE_DIR/service"

cp -- "$REPO_ROOT/android/main.py" "$SOURCE_DIR/main.py"
cp -- "$REPO_ROOT/android/service/recorder_service.py" "$SOURCE_DIR/service/recorder_service.py"
cp -R -- "$REPO_ROOT/android/java" "$SOURCE_DIR/java"
cp -R -- "$REPO_ROOT/android/manifest" "$SOURCE_DIR/manifest"
cp -R -- "$REPO_ROOT/douyinliverecorder" "$SOURCE_DIR/douyinliverecorder"
cp -R -- "$REPO_ROOT/src" "$SOURCE_DIR/src"

python3 "$REPO_ROOT/scripts/prepare_packaged_config.py" \
    --source "$REPO_ROOT/config" \
    --output "$SOURCE_DIR/config"
cp -- "$REPO_ROOT/android/buildozer.spec" "$PROJECT_DIR/buildozer.spec"

(
    cd "$PROJECT_DIR"
    "$BUILDOZER_BIN" android debug
)

APK_PATH="$(find "$PROJECT_DIR/bin" -maxdepth 1 -type f -name '*.apk' -print -quit)"
if [[ -z "$APK_PATH" ]]; then
    echo "Buildozer completed without producing an APK." >&2
    exit 1
fi

DIST_DIR="$REPO_ROOT/dist/android"
mkdir -p "$DIST_DIR"
cp -- "$APK_PATH" "$DIST_DIR/DouyinLiveRecorder-android-debug.apk"
printf 'Android build complete: %s\n' "$DIST_DIR/DouyinLiveRecorder-android-debug.apk"
