import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.bilibili import BilibiliAdapter
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
            anchor_name="bili-anchor",
            title="bili-title",
            is_live=True,
            streams=(
                ResolverStream("https://pull.example/bili.m3u8", "hls", "1080p", 1080),
                ResolverStream("https://pull.example/bili.flv", "flv", "1080p", 1080),
            ),
        )


class BilibiliAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_only_bilibili_live_host(self):
        adapter = BilibiliAdapter(FakeBackend())

        self.assertTrue(adapter.matches("https://live.bilibili.com/123"))
        self.assertTrue(adapter.matches("live.bilibili.com/123"))
        self.assertFalse(adapter.matches("https://www.bilibili.com/video/1"))
        self.assertFalse(adapter.matches("https://example.com/live.bilibili.com/123"))
        self.assertFalse(adapter.matches("not a url"))

    async def test_resolves_with_platform_context_and_prefers_flv(self):
        backend = FakeBackend()
        adapter = BilibiliAdapter(backend)
        target = RecordingTarget(url="https://live.bilibili.com/123")
        context = ResolveContext(
            quality=Quality.HIGH,
            proxy_addr="socks5://127.0.0.1:1080",
            cookies={"bilibili": "SESSDATA=bili"},
        )

        stream = await adapter.resolve(target, context)

        self.assertEqual(stream.platform_key, "bilibili")
        self.assertEqual(stream.platform_name, "Bilibili Live")
        self.assertEqual(stream.anchor_name, "bili-anchor")
        self.assertEqual(stream.title, "bili-title")
        self.assertTrue(stream.is_live)
        self.assertEqual(stream.quality, Quality.HIGH)
        self.assertEqual(stream.primary_url, "https://pull.example/bili.flv")
        self.assertEqual(stream.flv_url, "https://pull.example/bili.flv")
        self.assertEqual(stream.hls_url, "https://pull.example/bili.m3u8")
        self.assertEqual(stream.headers["Referer"], "https://live.bilibili.com/")
        self.assertEqual(
            backend.calls,
            [
                (
                    target.url,
                    "socks5://127.0.0.1:1080",
                    "SESSDATA=bili",
                    {"Referer": "https://live.bilibili.com/"},
                )
            ],
        )

    async def test_offline_result_with_no_streams_returns_offline(self):
        adapter = BilibiliAdapter(
            FakeBackend(
                ResolverResult(
                    anchor_name="bili-anchor", title="offline", is_live=False
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://live.bilibili.com/123"), ResolveContext()
        )

        self.assertFalse(stream.is_live)
        self.assertEqual(stream.primary_url, "")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")


if __name__ == "__main__":
    unittest.main()
