import tempfile
import unittest
from pathlib import Path

from douyinliverecorder.apps.android.service import run_service
from douyinliverecorder.apps.android.state import read_status


class AndroidServiceTests(unittest.TestCase):
    def test_existing_stop_request_exits_without_network_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_request = root / "stop.request"
            stop_request.write_text("stop\n", encoding="ascii")

            run_service(root)

            self.assertFalse(stop_request.exists())
            status = read_status(root / "service_status.json")
            self.assertFalse(status["monitoring"])
            self.assertEqual(status["active_recordings"], 0)


if __name__ == "__main__":
    unittest.main()
