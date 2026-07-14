#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
cd "$REPO_ROOT"

is_kivy_compatible_version() {
    local version="$1"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$version"
    [[ "$major" == "3" ]] && (( 10#$minor >= 10 && 10#$minor <= 13 ))
}

if [[ -n "${PYTHON:-}" ]]; then
    PYTHON_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Python was not found on PATH." >&2
    exit 1
fi

if ! PYTHON_INFO="$("$PYTHON_BIN" -c 'import os, sys; print("{}.{}.{}".format(*sys.version_info[:3])); print(os.path.realpath(sys.executable))')"; then
    echo "Unable to run Python interpreter: $PYTHON_BIN" >&2
    echo "Install Python 3.10-3.13 or set PYTHON=/path/to/python3.13." >&2
    exit 1
fi

PYTHON_VERSION="${PYTHON_INFO%%$'\n'*}"
PYTHON_PATH="${PYTHON_INFO#*$'\n'}"

if ! is_kivy_compatible_version "$PYTHON_VERSION"; then
    echo "Python 3.10-3.13 is required for Kivy 2.3.1; found Python $PYTHON_VERSION at $PYTHON_PATH." >&2
    echo "Set PYTHON=/path/to/python3.13 and run this script again." >&2
    exit 1
fi

printf 'Using base Python %s at %s\n' "$PYTHON_VERSION" "$PYTHON_PATH"

FINGERPRINT_SOURCE="${PYTHON_PATH}|${PYTHON_VERSION}"
BASE_FINGERPRINT="$(
    printf '%s' "$FINGERPRINT_SOURCE" |
        "$PYTHON_BIN" -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
)"

BUILD_VENV_ROOT="$REPO_ROOT/.build-venv"
EXPECTED_BUILD_VENV="$REPO_ROOT/.build-venv/linux"
BUILD_VENV="$EXPECTED_BUILD_VENV"
BUILD_PYTHON="$BUILD_VENV/bin/python"
FINGERPRINT_FILE="$BUILD_VENV/base-python.fingerprint"

VENV_REUSABLE=0
BUILD_PYTHON_VERSION=""
if [[ -x "$BUILD_PYTHON" && -f "$FINGERPRINT_FILE" ]]; then
    if BUILD_PYTHON_VERSION="$("$BUILD_PYTHON" -c 'import sys; print("{}.{}.{}".format(*sys.version_info[:3]))')" &&
        is_kivy_compatible_version "$BUILD_PYTHON_VERSION"; then
        STORED_FINGERPRINT="$(<"$FINGERPRINT_FILE")"
        if [[ "$STORED_FINGERPRINT" == "$BASE_FINGERPRINT" ]]; then
            VENV_REUSABLE=1
        fi
    fi
fi

if [[ "$VENV_REUSABLE" != "1" ]]; then
    if [[ "$BUILD_VENV" != "$EXPECTED_BUILD_VENV" ]]; then
        echo "Refusing to remove unexpected build virtual environment path: $BUILD_VENV" >&2
        exit 1
    fi

    if [[ -L "$BUILD_VENV_ROOT" || -L "$BUILD_VENV" ]]; then
        echo "Refusing to use a symlink for the build virtual environment: $BUILD_VENV" >&2
        exit 1
    fi

    if [[ -e "$BUILD_VENV" ]]; then
        if ! REAL_REPO_ROOT="$(realpath -e -- "$REPO_ROOT")"; then
            echo "Unable to resolve repository path: $REPO_ROOT" >&2
            exit 1
        fi
        if ! REAL_BUILD_VENV_ROOT="$(realpath -e -- "$BUILD_VENV_ROOT")"; then
            echo "Unable to resolve build virtual environment parent: $BUILD_VENV_ROOT" >&2
            exit 1
        fi
        if ! REAL_BUILD_VENV="$(realpath -e -- "$BUILD_VENV")"; then
            echo "Unable to resolve build virtual environment path: $BUILD_VENV" >&2
            exit 1
        fi

        EXPECTED_REAL_BUILD_VENV_ROOT="$REAL_REPO_ROOT/.build-venv"
        EXPECTED_REAL_BUILD_VENV="$EXPECTED_REAL_BUILD_VENV_ROOT/linux"
        if [[ "$REAL_BUILD_VENV_ROOT" != "$EXPECTED_REAL_BUILD_VENV_ROOT" ||
            "$REAL_BUILD_VENV" != "$EXPECTED_REAL_BUILD_VENV" ]]; then
            echo "Refusing to remove build virtual environment outside the repository: $REAL_BUILD_VENV" >&2
            exit 1
        fi

        rm -rf -- "$BUILD_VENV"
    fi

    "$PYTHON_BIN" -m venv "$BUILD_VENV"

    if [[ ! -x "$BUILD_PYTHON" ]]; then
        echo "Build Python executable not found: $BUILD_PYTHON" >&2
        exit 1
    fi

    if ! BUILD_PYTHON_VERSION="$("$BUILD_PYTHON" -c 'import sys; print("{}.{}.{}".format(*sys.version_info[:3]))')"; then
        echo "Unable to run build Python interpreter: $BUILD_PYTHON" >&2
        exit 1
    fi
    if ! is_kivy_compatible_version "$BUILD_PYTHON_VERSION"; then
        echo "Build Python $BUILD_PYTHON_VERSION is not compatible with Kivy 2.3.1." >&2
        exit 1
    fi

    FINGERPRINT_TEMP="$FINGERPRINT_FILE.tmp.$$"
    if ! printf '%s\n' "$BASE_FINGERPRINT" > "$FINGERPRINT_TEMP"; then
        echo "Unable to write build Python fingerprint: $FINGERPRINT_TEMP" >&2
        exit 1
    fi
    if ! mv -f -- "$FINGERPRINT_TEMP" "$FINGERPRINT_FILE"; then
        rm -f -- "$FINGERPRINT_TEMP"
        echo "Unable to install build Python fingerprint: $FINGERPRINT_FILE" >&2
        exit 1
    fi
fi

printf 'Using build Python %s at %s\n' "$BUILD_PYTHON_VERSION" "$BUILD_PYTHON"

ENTRY_POINT="lubo/apps/desktop/main.py"
RESOURCE_DIRECTORIES=("config")

if [[ ! -f "$ENTRY_POINT" ]]; then
    echo "Desktop entry point not found: $ENTRY_POINT" >&2
    exit 1
fi

for directory in "${RESOURCE_DIRECTORIES[@]}"; do
    if [[ ! -d "$directory" ]]; then
        echo "Resource directory not found: $directory" >&2
        exit 1
    fi
done

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    if [[ ! -f "requirements-gui.txt" ]]; then
        echo "Requirements file not found: requirements-gui.txt" >&2
        exit 1
    fi

    "$BUILD_PYTHON" -m pip install -r requirements-gui.txt
fi

if ! FFMPEG_PATH="$(command -v ffmpeg)"; then
    echo "FFmpeg was not found on PATH. Install FFmpeg before building the application." >&2
    exit 1
fi
FFMPEG_PATH="$(realpath -e -- "$FFMPEG_PATH")"
printf 'Bundling FFmpeg from %s\n' "$FFMPEG_PATH"

PACKAGED_CONFIG="build/package-config"
"$BUILD_PYTHON" scripts/prepare_packaged_config.py \
    --source config \
    --output "$PACKAGED_CONFIG"

KIVY_LOG_MODE=PYTHON \
KIVY_NO_FILELOG=1 \
"$BUILD_PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --log-level INFO \
    --name Lubo \
    --onedir \
    --windowed \
    --additional-hooks-dir "packaging/pyinstaller-hooks" \
    --collect-data kivy \
    --add-binary "$FFMPEG_PATH:." \
    --add-data "$PACKAGED_CONFIG:config" \
    "lubo/apps/desktop/main.py"

DIST_PATH="$REPO_ROOT/dist/Lubo"
if [[ ! -d "$DIST_PATH" ]]; then
    echo "Expected build output not found: $DIST_PATH" >&2
    exit 1
fi

printf 'Build complete: %s\n' "$DIST_PATH"
