from __future__ import annotations

import shutil
import sys
from pathlib import Path


def resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root)
    return Path(__file__).resolve().parents[3]


def resolve_ffmpeg(root: Path | None = None) -> str:
    executable_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bundled = (root or resource_root()) / executable_name
    if bundled.is_file():
        return str(bundled)

    installed = shutil.which("ffmpeg")
    if installed:
        return installed

    raise RuntimeError(
        "FFmpeg was not found in the application package or on PATH. "
        "Reinstall the application with FFmpeg included."
    )
