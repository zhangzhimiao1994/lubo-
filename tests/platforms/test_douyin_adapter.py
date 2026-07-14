import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.douyin import DouyinAdapter
from lubo.resolvers.base import ResolverResult, ResolverStream


class FakeBackend:
    def __init__(self, result=None):
        self.result = result or ResolverResult(
            anchor_name="anchor-a",
            title="live-title",
            is_live=True,
            streams=(
                ResolverStream(
                    url="https://pull.example/live.m3u8",
                    protocol="hls",
                    quality_name="1080p",
                    height=1080,
                ),
                ResolverStream(
                    url="https://pull.example/live.flv",
                    protocol="flv",
                    quality_name="1080p",
                    height=1080,
                ),
            ),
        )
        self.calls = []

    async def resolve(self, url, *, proxy_addr="", cookies="", headers=None):
        self.calls.append(
            {
                "url": url,
                "proxy_addr": proxy_addr,
                "cookies": cookies,
                "headers": headers,
            }
        )
        return self.result


class DouyinAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_only_supported_hosts(self):
        adapter = DouyinAdapter(FakeBackend())

        for url in (
            "https://live.douyin.com/123",
            "live.douyin.com/123",
            "https://v.douyin.com/abc",
            "https://www.douyin.com/user/example",
        ):
            with self.subTest(url=url):
                self.assertTrue(adapter.matches(url))

        for url in (
            "https://douyin.com/123",
            "https://live.bilibili.com/1",
            "https://example.com/live.douyin.com/123",
            "https://[invalid",
            "",
        ):
            with self.subTest(url=url):
                self.assertFalse(adapter.matches(url))

    async def test_resolves_with_platform_context_and_prefers_flv(self):
        backend = FakeBackend()
        adapter = DouyinAdapter(backend)
        target = RecordingTarget(url="https://live.douyin.com/123", quality=Quality.HIGH)
        context = ResolveContext(
            quality=Quality.HIGH,
            proxy_addr="http://127.0.0.1:7890",
            cookies={"douyin": "sessionid=douyin"},
        )

        stream = await adapter.resolve(target, context)

        self.assertEqual(stream.platform_key, "douyin")
        self.assertEqual(stream.platform_name, "Douyin")
        self.assertEqual(stream.anchor_name, "anchor-a")
        self.assertEqual(stream.title, "live-title")
        self.assertTrue(stream.is_live)
        self.assertEqual(stream.quality, Quality.HIGH)
        self.assertEqual(stream.primary_url, "https://pull.example/live.flv")
        self.assertEqual(stream.flv_url, "https://pull.example/live.flv")
        self.assertEqual(stream.hls_url, "https://pull.example/live.m3u8")
        self.assertEqual(stream.headers["Referer"], "https://live.douyin.com/")
        self.assertEqual(
            backend.calls,
            [
                {
                    "url": target.url,
                    "proxy_addr": "http://127.0.0.1:7890",
                    "cookies": "sessionid=douyin",
                    "headers": {"Referer": "https://live.douyin.com/"},
                }
            ],
        )

    async def test_offline_result_with_no_streams_does_not_select_a_stream(self):
        backend = FakeBackend(
            ResolverResult(anchor_name="anchor-a", title="offline", is_live=False)
        )
        adapter = DouyinAdapter(backend)

        stream = await adapter.resolve(
            RecordingTarget(url="https://live.douyin.com/123"), ResolveContext()
        )

        self.assertFalse(stream.is_live)
        self.assertEqual(stream.primary_url, "")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")


if __name__ == "__main__":
    unittest.main()
