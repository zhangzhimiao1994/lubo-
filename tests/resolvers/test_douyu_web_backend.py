import unittest

from lubo.resolvers.base import PlatformAccessError
from lubo.resolvers.douyu_web_backend import DouyuWebBackend


class FakeFetcher:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class DouyuWebBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_live_room_from_official_preview_api(self):
        fetcher = FakeFetcher(
            [
                {
                    "error": 0,
                    "data": {
                        "room_id": "36252",
                        "room_status": "1",
                        "owner_name": "MrGemini",
                        "room_name": "Gemini live",
                    },
                },
                {
                    "error": 0,
                    "data": {
                        "rtmp_url": "https://hlshwa.douyucdn2.cn/live",
                        "rtmp_live": "36252abc.m3u8?token=temporary",
                        "rate": 0,
                    },
                },
            ]
        )
        backend = DouyuWebBackend(
            fetcher,
            clock_ms=lambda: 1_784_095_349_123,
            device_id="10000000000000000000000000001501",
        )

        result = await backend.resolve(
            "https://www.douyu.com/36252",
            proxy_addr="http://127.0.0.1:7890",
            cookies="acf_uid=test",
            headers={"X-Test": "value"},
        )

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "MrGemini")
        self.assertEqual(result.title, "Gemini live")
        self.assertEqual(len(result.streams), 1)
        stream = result.streams[0]
        self.assertEqual(
            stream.url,
            "https://hlshwa.douyucdn2.cn/live/36252abc.m3u8?token=temporary",
        )
        self.assertEqual(stream.protocol, "hls")
        self.assertEqual(stream.quality_name, "original")
        self.assertEqual(stream.headers["Referer"], "https://www.douyu.com/36252")
        self.assertIn("User-Agent", stream.headers)

        metadata_url, metadata = fetcher.calls[0]
        self.assertEqual(
            metadata_url,
            "https://open.douyucdn.cn/api/RoomApi/room/36252",
        )
        self.assertEqual(metadata["method"], "GET")
        self.assertEqual(metadata["proxy_addr"], "http://127.0.0.1:7890")
        self.assertEqual(metadata["headers"]["Cookie"], "acf_uid=test")
        self.assertEqual(metadata["headers"]["X-Test"], "value")

        preview_url, preview = fetcher.calls[1]
        self.assertEqual(
            preview_url,
            "https://playweb.douyucdn.cn/lapi/live/hlsH5Preview/36252",
        )
        self.assertEqual(preview["method"], "POST")
        self.assertEqual(preview["data"]["rid"], "36252")
        self.assertEqual(
            preview["data"]["did"],
            "10000000000000000000000000001501",
        )
        self.assertEqual(preview["headers"]["rid"], "36252")
        self.assertEqual(preview["headers"]["time"], "1784095349123")
        self.assertEqual(
            preview["headers"]["auth"],
            "5a37875247d849507e311562cd36837c",
        )

    async def test_offline_room_does_not_call_preview_api(self):
        fetcher = FakeFetcher(
            [
                {
                    "error": 0,
                    "data": {
                        "room_id": "171717",
                        "room_status": "0",
                        "owner_name": "offline-anchor",
                        "room_name": "offline-room",
                    },
                }
            ]
        )

        result = await DouyuWebBackend(fetcher).resolve(
            "https://www.douyu.com/171717"
        )

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "offline-anchor")
        self.assertEqual(result.title, "offline-room")
        self.assertEqual(len(fetcher.calls), 1)

    async def test_preview_offline_response_returns_offline_metadata(self):
        fetcher = FakeFetcher(
            [
                {
                    "error": 0,
                    "data": {
                        "room_id": "123",
                        "room_status": "1",
                        "owner_name": "anchor",
                        "room_name": "room",
                    },
                },
                {"error": 104, "msg": "offline", "data": ""},
            ]
        )

        result = await DouyuWebBackend(fetcher).resolve(
            "https://www.douyu.com/123"
        )

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "anchor")
        self.assertEqual(result.title, "room")

    async def test_rejects_untrusted_input_before_fetching(self):
        fetcher = FakeFetcher([])

        with self.assertRaisesRegex(PlatformAccessError, "could not be accessed"):
            await DouyuWebBackend(fetcher).resolve(
                "https://example.com/www.douyu.com/36252"
            )

        self.assertEqual(fetcher.calls, [])

    async def test_rejects_untrusted_stream_host(self):
        fetcher = FakeFetcher(
            [
                {
                    "error": 0,
                    "data": {"room_status": "1", "room_id": "123"},
                },
                {
                    "error": 0,
                    "data": {
                        "rtmp_url": "https://attacker.example/live",
                        "rtmp_live": "123.m3u8",
                    },
                },
            ]
        )

        with self.assertRaisesRegex(PlatformAccessError, "could not be parsed"):
            await DouyuWebBackend(fetcher).resolve("https://www.douyu.com/123")

    async def test_maps_fetch_errors_to_stable_access_error(self):
        def fetcher(*args, **kwargs):
            raise OSError("secret upstream detail")

        with self.assertRaisesRegex(PlatformAccessError, "could not be accessed"):
            await DouyuWebBackend(fetcher).resolve("https://www.douyu.com/123")


if __name__ == "__main__":
    unittest.main()
