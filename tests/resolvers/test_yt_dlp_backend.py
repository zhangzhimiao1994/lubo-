import threading
import unittest

from lubo.resolvers.base import PlatformAccessError
from lubo.resolvers.yt_dlp_backend import YtDlpBackend


class UserNotLive(Exception):
    pass


UserNotLive.__module__ = "yt_dlp.utils._utils"


class FakeYoutubeDL:
    def __init__(self, info):
        self.info = info
        self.extracted_url = None
        self.download = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def extract_info(self, url, download):
        self.extracted_url = url
        self.download = download
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


class YdlFactory:
    def __init__(self, info):
        self.ydl = FakeYoutubeDL(info)
        self.options = None
        self.thread_id = None

    def __call__(self, options):
        self.options = options
        self.thread_id = threading.get_ident()
        return self.ydl


class YtDlpBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_maps_live_formats_metadata_and_exact_options(self):
        factory = YdlFactory(
            {
                "uploader": "Uploader Name",
                "channel": "Channel Name",
                "creator": "Creator Name",
                "title": "Live Title",
                "is_live": True,
                "formats": [
                    {
                        "url": "https://cdn.example/live.flv?token=ok",
                        "format_note": "1080p",
                        "format": "fallback format",
                        "height": 1080,
                        "protocol": "https",
                        "ext": "flv",
                        "http_headers": {"Referer": "https://live.example"},
                    },
                    {
                        "url": "https://cdn.example/live.m3u8",
                        "format": "720p hls",
                        "height": 720,
                        "protocol": "m3u8_native",
                        "ext": "mp4",
                        "http_headers": {"User-Agent": "fake-agent"},
                    },
                ],
            }
        )
        main_thread_id = threading.get_ident()

        result = await YtDlpBackend(factory).resolve(
            "https://video.example/live",
            proxy_addr="http://127.0.0.1:7890",
            cookies="session=cookie-value",
            headers={"Referer": "https://video.example", "X-Test": "yes"},
        )

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "Uploader Name")
        self.assertEqual(result.title, "Live Title")
        self.assertEqual(result.streams[0].protocol, "flv")
        self.assertEqual(result.streams[0].quality_name, "1080p")
        self.assertEqual(result.streams[0].height, 1080)
        self.assertEqual(result.streams[0].headers["Referer"], "https://live.example")
        self.assertEqual(result.streams[1].protocol, "hls")
        self.assertEqual(result.streams[1].quality_name, "720p hls")
        self.assertEqual(
            factory.options,
            {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "proxy": "http://127.0.0.1:7890",
                "http_headers": {
                    "Referer": "https://video.example",
                    "X-Test": "yes",
                    "Cookie": "session=cookie-value",
                },
            },
        )
        self.assertEqual(factory.ydl.extracted_url, "https://video.example/live")
        self.assertFalse(factory.ydl.download)
        self.assertNotEqual(factory.thread_id, main_thread_id)

    async def test_resolve_returns_offline_result(self):
        factory = YdlFactory(
            {
                "channel": "Offline Channel",
                "title": "Replay",
                "live_status": "not_live",
                "formats": [
                    {
                        "url": "https://cdn.example/replay.flv",
                        "format": "replay",
                        "ext": "flv",
                    }
                ],
            }
        )

        result = await YtDlpBackend(factory).resolve("https://video.example/offline")

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "Offline Channel")
        self.assertEqual(result.title, "Replay")
        self.assertEqual(result.streams, ())
        self.assertIsNone(factory.options["proxy"])
        self.assertEqual(factory.options["http_headers"], {})

    async def test_resolve_returns_offline_result_for_user_not_live_error(self):
        factory = YdlFactory(UserNotLive("This live event will begin soon"))

        result = await YtDlpBackend(factory).resolve("https://video.example/offline")

        self.assertFalse(result.is_live)
        self.assertEqual(result.streams, ())

    async def test_resolve_normalizes_format_height_and_headers(self):
        factory = YdlFactory(
            {
                "is_live": True,
                "formats": [
                    {
                        "url": "https://cdn.example/valid.mp4",
                        "format_note": "valid",
                        "height": "720",
                        "protocol": "https",
                        "ext": "mp4",
                        "http_headers": {
                            "Referer": "https://video.example",
                            "X-Count": 3,
                            7: "invalid-key",
                        },
                    },
                    {
                        "url": "https://cdn.example/invalid.mp4",
                        "format_note": "invalid",
                        "height": "unknown",
                        "protocol": "https",
                        "ext": "mp4",
                        "http_headers": "not-a-mapping",
                    },
                ],
            }
        )

        result = await YtDlpBackend(factory).resolve("https://video.example/live")

        self.assertEqual(result.streams[0].height, 720)
        self.assertIsInstance(result.streams[0].height, int)
        self.assertEqual(
            dict(result.streams[0].headers),
            {"Referer": "https://video.example"},
        )
        self.assertIsNone(result.streams[1].height)
        self.assertEqual(dict(result.streams[1].headers), {})

    async def test_resolve_redacts_engine_errors(self):
        source_url = "https://video.example/live?signature=url-secret"
        cookie_secret = "cookie-secret"
        factory = YdlFactory(
            RuntimeError(
                "session-secret at https://cdn.example/live.m3u8?signature=engine-secret"
            )
        )

        with self.assertRaises(PlatformAccessError) as raised:
            await YtDlpBackend(factory).resolve(
                source_url,
                cookies=f"session={cookie_secret}",
            )

        public_message = str(raised.exception)
        self.assertNotIn(source_url, public_message)
        self.assertNotIn(cookie_secret, public_message)
        self.assertNotIn("session-secret", public_message)
        self.assertNotIn("engine-secret", public_message)


if __name__ == "__main__":
    unittest.main()
