import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from douyinliverecorder.apps.android.platform import (
    SERVICE_CLASS,
    request_service_stop,
    start_recorder_service,
)


class AndroidPlatformTests(unittest.TestCase):
    def test_request_service_stop_writes_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "app"

            request_service_stop(root)

            self.assertEqual((root / "stop.request").read_text(encoding="ascii"), "stop\n")

    def test_start_service_clears_marker_and_uses_generated_service(self):
        calls = []

        class FakeService:
            @staticmethod
            def start(*args):
                calls.append(args)

        activity = object()

        class FakeActivity:
            mActivity = activity

        def autoclass(name):
            if name == SERVICE_CLASS:
                return FakeService
            if name == "org.kivy.android.PythonActivity":
                return FakeActivity
            raise AssertionError(name)

        jnius = types.ModuleType("jnius")
        jnius.autoclass = autoclass
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "stop.request"
            marker.write_text("stop\n", encoding="ascii")
            with patch.dict(sys.modules, {"jnius": jnius}):
                start_recorder_service(root)

            self.assertFalse(marker.exists())
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0][0], activity)
            self.assertIn("Douyin Live Recorder", calls[0])


if __name__ == "__main__":
    unittest.main()
