import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.douyu import DouyuAdapter
from lubo.resolvers.base import ResolverResult, ResolverStream


class FakeBackend:
    def __init__(self):
        self.calls = []

    async def resolve(self, url, *, proxy_addr="", cookies="", headers=None):
        self.calls.append((url, proxy_addr, cookies, headers))
        return ResolverResult(
            anchor_name="douyu-anchor",
            title="douyu-title",
            is_live=True,
            streams=(
                ResolverStream("https://pull.example/douyu.m3u8", "hls", "1080p", 1080),
                ResolverStream(
                    "https://pull.example/douyu.flv",
                    "flv",
                    "1080p",
                    1080,
                    {"User-Agent": "yt-dlp-agent", "X-Stream-Token": "token"},
                ),
            ),
        )


class DouyuAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_only_supported_douyu_hosts(self):
        adapter = DouyuAdapter(FakeBackend())

        for url in (
            "https://douyu.com/123",
            "https://www.douyu.com/123",
            "https://m.douyu.com/123",
        ):
            with self.subTest(url=url):
                self.assertTrue(adapter.matches(url))

        self.assertFalse(adapter.matches("https://live.douyu.com/123"))
        self.assertFalse(adapter.matches("https://example.com/www.douyu.com/123"))
        self.assertFalse(adapter.matches("https://[invalid"))

    async def test_resolves_and_merges_selected_stream_headers(self):
        backend = FakeBackend()
        adapter = DouyuAdapter(backend)
        target = RecordingTarget(url="https://www.douyu.com/123")
        context = ResolveContext(
            quality=Quality.HIGH,
            proxy_addr="http://127.0.0.1:7890",
            cookies={"douyu": "acf_uid=douyu"},
        )

        stream = await adapter.resolve(target, context)

        self.assertEqual(stream.platform_key, "douyu")
        self.assertEqual(stream.platform_name, "Douyu")
        self.assertEqual(stream.anchor_name, "douyu-anchor")
        self.assertEqual(stream.title, "douyu-title")
        self.assertTrue(stream.is_live)
        self.assertEqual(stream.quality, Quality.HIGH)
        self.assertEqual(stream.primary_url, "https://pull.example/douyu.flv")
        self.assertEqual(stream.flv_url, "https://pull.example/douyu.flv")
        self.assertEqual(stream.hls_url, "https://pull.example/douyu.m3u8")
        self.assertEqual(
            dict(stream.headers),
            {
                "User-Agent": "yt-dlp-agent",
                "X-Stream-Token": "token",
                "Referer": "https://www.douyu.com/",
            },
        )
        self.assertEqual(
            backend.calls,
            [
                (
                    target.url,
                    "http://127.0.0.1:7890",
                    "acf_uid=douyu",
                    {"Referer": "https://www.douyu.com/"},
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
