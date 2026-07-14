import json
import tempfile
import unittest
from pathlib import Path

from lubo.apps.android.state import read_status, write_status


class AndroidStateTests(unittest.TestCase):
    def test_missing_and_malformed_status_use_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            self.assertFalse(read_status(path)["monitoring"])
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(read_status(path)["message"], "Stopped")

    def test_write_status_is_atomic_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "status.json"

            write_status(
                path,
                {"monitoring": True, "active_recordings": 2, "message": "recording"},
            )

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["active_recordings"], 2)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            self.assertTrue(read_status(path)["monitoring"])


if __name__ == "__main__":
    unittest.main()
