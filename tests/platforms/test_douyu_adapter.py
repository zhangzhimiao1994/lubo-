import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.douyu import DouyuAdapter
from lubo.resolvers.base import ResolverResult, ResolverStream


class FakeBackend:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    async def resolve(self, url, *, proxy_addr="", cookies="", headers=None):
        self.calls.append((url, proxy_addr, cookies, headers))
        if self.result is not None:
            return self.result
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

    async def test_offline_result_with_no_streams_returns_offline(self):
        adapter = DouyuAdapter(
            FakeBackend(
                ResolverResult(
                    anchor_name="douyu-anchor", title="offline", is_live=False
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://www.douyu.com/123"), ResolveContext()
        )

        self.assertFalse(stream.is_live)
        self.assertEqual(stream.primary_url, "")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")

    async def test_does_not_fill_flv_url_from_lower_height_than_selected_http(self):
        adapter = DouyuAdapter(
            FakeBackend(
                ResolverResult(
                    is_live=True,
                    streams=(
                        ResolverStream(
                            "https://pull.example/live-1080.mp4",
                            "http",
                            "1080p",
                            1080,
                            {"User-Agent": "http-agent", "X-Selected": "1080"},
                        ),
                        ResolverStream(
                            "https://pull.example/live-720.flv",
                            "flv",
                            "720p",
                            720,
                            {"User-Agent": "low-flv-agent"},
                        ),
                    ),
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://www.douyu.com/123"),
            ResolveContext(quality=Quality.HIGH),
        )

        self.assertEqual(stream.primary_url, "https://pull.example/live-1080.mp4")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")
        self.assertEqual(
            dict(stream.headers),
            {
                "User-Agent": "http-agent",
                "X-Selected": "1080",
                "Referer": "https://www.douyu.com/",
            },
        )

    async def test_does_not_fill_flv_url_from_lower_height_than_selected_hls(self):
        adapter = DouyuAdapter(
            FakeBackend(
                ResolverResult(
                    is_live=True,
                    streams=(
                        ResolverStream(
                            "https://pull.example/live-1080.m3u8",
                            "hls",
                            "1080p",
                            1080,
                            {"User-Agent": "hls-agent"},
                        ),
                        ResolverStream(
                            "https://pull.example/live-720.flv",
                            "flv",
                            "720p",
                            720,
                        ),
                    ),
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://www.douyu.com/123"),
            ResolveContext(quality=Quality.HIGH),
        )

        self.assertEqual(stream.primary_url, "https://pull.example/live-1080.m3u8")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "https://pull.example/live-1080.m3u8")
        self.assertEqual(
            dict(stream.headers),
            {
                "User-Agent": "hls-agent",
                "Referer": "https://www.douyu.com/",
            },
        )

    async def test_unknown_height_does_not_fill_url_from_another_candidate(self):
        adapter = DouyuAdapter(
            FakeBackend(
                ResolverResult(
                    is_live=True,
                    streams=(
                        ResolverStream(
                            "https://pull.example/selected",
                            "http",
                            headers={"X-Selected": "unknown-height"},
                        ),
                        ResolverStream(
                            "https://pull.example/other.m3u8",
                            "hls",
                        ),
                    ),
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://www.douyu.com/123"), ResolveContext()
        )

        self.assertEqual(stream.primary_url, "https://pull.example/selected")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")
        self.assertEqual(stream.headers["X-Selected"], "unknown-height")


if __name__ == "__main__":
    unittest.main()
