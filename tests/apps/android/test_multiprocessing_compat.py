import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

ANDROID_RUNTIME_CHECK = textwrap.dedent(
    r"""
    import builtins
    import json
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    real_import = builtins.__import__
    blocked_imports = []

    def android_import(name, globals=None, locals=None, fromlist=(), level=0):
        root_name = name.partition(".")[0]
        if root_name in {"multiprocessing", "_multiprocessing"}:
            blocked_imports.append(name)
            error = ModuleNotFoundError(f"No module named '{root_name}'")
            error.name = root_name
            raise error
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = android_import

    try:
        from importlib.metadata import version
        import streamlink
        import yt_dlp
        from streamlink import Streamlink
        from yt_dlp import YoutubeDL
    except ModuleNotFoundError as error:
        if error.name in {"multiprocessing", "_multiprocessing"}:
            raise
        raise RuntimeError(
            "Android compatibility test requires the complete pinned runtime "
            f"dependencies; missing module: {error.name}"
        ) from error

    assert version("streamlink") == "8.4.0"
    assert version("yt-dlp") == "2026.6.9"
    assert streamlink is not None
    assert yt_dlp is not None

    runtime_streamlink = Streamlink()
    with YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
    ) as runtime_ytdlp:
        assert runtime_ytdlp is not None

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
        def __init__(self, stop_request):
            self.stop_request = stop_request
            self.http = FakeHttp()
            self.requested_url = ""

        def resolve_url(self, url):
            self.requested_url = url
            return "fake-douyin", OfflinePlugin, url

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
        fake_session = OfflineStreamlinkSession(stop_request)
        backend = StreamlinkBackend(lambda: fake_session)

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
            patch.object(
                android_service.time,
                "sleep",
                side_effect=stop_after_failed_check,
            ),
        ):
            android_service.run_service(root)

        status = read_status(root / "service_status.json")

    assert runtime_streamlink is not None
    assert blocked_imports == []
    assert fake_session.requested_url == room_url
    assert fake_session.http.headers == {"Referer": "https://live.douyin.com/"}
    assert fake_session.http.cookies == {}
    assert status["monitoring"] is False
    assert status["active_recordings"] == 0
    print(json.dumps({"blocked_imports": blocked_imports, "room_url": room_url}))
    """
)


class AndroidMultiprocessingCompatibilityTests(unittest.TestCase):
    def test_service_resolves_douyin_without_multiprocessing_runtime(self):
        completed = subprocess.run(
            [sys.executable, "-c", ANDROID_RUNTIME_CHECK],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            "Android runtime compatibility subprocess failed. "
            "Install the exact requirements.txt dependencies before running "
            f"this test.\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["blocked_imports"], [])
        self.assertEqual(result["room_url"], "https://live.douyin.com/91199")


if __name__ == "__main__":
    unittest.main()
