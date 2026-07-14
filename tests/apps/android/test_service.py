import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lubo.apps.android import service as android_service
from lubo.apps.android.state import read_status
from lubo.core.config import AppConfig


class AndroidServiceTests(unittest.TestCase):
    def test_existing_stop_request_exits_without_network_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stop_request = root / "stop.request"
            stop_request.write_text("stop\n", encoding="ascii")

            android_service.run_service(root)

            self.assertFalse(stop_request.exists())
            status = read_status(root / "service_status.json")
            self.assertFalse(status["monitoring"])
            self.assertEqual(status["active_recordings"], 0)

    def test_normal_initialization_uses_default_registry_and_all_platform_cookies(self):
        class OneCheckScheduler:
            def __init__(self, stop_request):
                self.stop_request = stop_request
                self.tasks = {}
                self.check_calls = 0
                self.shutdown_calls = 0

            async def check_once(self, _targets):
                self.check_calls += 1
                self.stop_request.write_text("stop\n", encoding="ascii")

            def shutdown(self):
                self.shutdown_calls += 1

        cookies = {
            "douyin": "douyin-cookie",
            "bilibili": "bilibili-cookie",
            "huya": "huya-cookie",
            "douyu": "douyu-cookie",
        }
        config = AppConfig(cookies=cookies)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scheduler = OneCheckScheduler(root / "stop.request")
            registry = object()

            with (
                patch.object(android_service, "ConfigService") as config_service_type,
                patch.object(
                    android_service,
                    "build_default_registry",
                    return_value=registry,
                ) as build_registry,
                patch.object(
                    android_service,
                    "RecordingScheduler",
                    return_value=scheduler,
                ) as scheduler_type,
            ):
                config_service_type.return_value.load.return_value = config
                android_service.run_service(root)

        build_registry.assert_called_once_with()
        scheduler_config = scheduler_type.call_args.kwargs["config"]
        self.assertIs(scheduler_type.call_args.kwargs["registry"], registry)
        self.assertEqual(scheduler_config.cookies, cookies)
        self.assertIsInstance(scheduler_config.cookies, dict)
        self.assertIsNot(scheduler_config.cookies, config.cookies)
        self.assertEqual(scheduler.check_calls, 1)
        self.assertEqual(scheduler.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()
