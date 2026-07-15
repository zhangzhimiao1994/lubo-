import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lubo.apps.android import service as android_service
from lubo.apps.android.state import read_status
from lubo.platforms.factory import build_default_registry
from lubo.resolvers.streamlink_backend import StreamlinkBackend


class FakeHttp:
    def __init__(self):
        self.headers = {}
        self.cookies = {}


class OfflinePlugin:
    def __init__(self, session, _resolved_url):
        self.session = session
        self.author = "android-test"
        self.title = "offline"

    def streams(self):
        self.session.stop_request.write_text("stop\n", encoding="ascii")
        return {}


class OfflineStreamlinkSession:
    def __init__(self, stop_request: Path):
        self.stop_request = stop_request
        self.http = FakeHttp()
        self.requested_url = ""

    def resolve_url(self, url):
        self.requested_url = url
        return "fake-douyin", OfflinePlugin, url


class AndroidMultiprocessingCompatibilityTests(unittest.TestCase):
    def test_service_resolves_douyin_without_multiprocessing_runtime(self):
        real_import = builtins.__import__
        blocked_imports = []

        def android_import(name, globals=None, locals=None, fromlist=(), level=0):
            root_name = name.partition(".")[0]
            if root_name in {"multiprocessing", "_multiprocessing"}:
                blocked_imports.append(name)
                raise ModuleNotFoundError(f"No module named '{root_name}'")
            return real_import(name, globals, locals, fromlist, level)

        for forbidden_name in ("multiprocessing", "_multiprocessing"):
            with self.subTest(import_guard=forbidden_name):
                with self.assertRaisesRegex(ModuleNotFoundError, forbidden_name):
                    android_import(forbidden_name)
        blocked_imports.clear()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir(parents=True)
            room_url = "https://live.douyin.com/91199"
            (config_dir / "URL_config.ini").write_text(
                f"{room_url}\n",
                encoding="utf-8",
            )
            stop_request = root / "stop.request"
            session = OfflineStreamlinkSession(stop_request)
            backend = StreamlinkBackend(lambda: session)

            def registry_factory():
                return build_default_registry(streamlink_backend=backend)

            def stop_after_failed_check(_seconds):
                stop_request.write_text("stop\n", encoding="ascii")

            with (
                patch.object(
                    android_service,
                    "build_default_registry",
                    side_effect=registry_factory,
                ),
                patch.object(builtins, "__import__", side_effect=android_import),
                patch.object(
                    android_service.time,
                    "sleep",
                    side_effect=stop_after_failed_check,
                ),
            ):
                android_service.run_service(root)

            status = read_status(root / "service_status.json")

        self.assertEqual(blocked_imports, [])
        self.assertEqual(session.requested_url, room_url)
        self.assertEqual(
            session.http.headers,
            {"Referer": "https://live.douyin.com/"},
        )
        self.assertEqual(session.http.cookies, {})
        self.assertFalse(status["monitoring"])
        self.assertEqual(status["active_recordings"], 0)


if __name__ == "__main__":
    unittest.main()
