import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from douyinliverecorder.apps.desktop.runtime import resolve_ffmpeg


class DesktopRuntimeTests(unittest.TestCase):
    def test_resolve_ffmpeg_prefers_bundled_executable(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundled = root / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            bundled.touch()

            with patch("shutil.which", return_value="path-ffmpeg"):
                result = resolve_ffmpeg(root)

        self.assertEqual(result, str(bundled))

    def test_resolve_ffmpeg_falls_back_to_path(self):
        with TemporaryDirectory() as temp_dir:
            with patch("shutil.which", return_value="C:/tools/ffmpeg.exe"):
                result = resolve_ffmpeg(Path(temp_dir))

        self.assertEqual(result, "C:/tools/ffmpeg.exe")

    def test_resolve_ffmpeg_fails_clearly_when_missing(self):
        with TemporaryDirectory() as temp_dir:
            with patch("shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "FFmpeg was not found"):
                    resolve_ffmpeg(Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
