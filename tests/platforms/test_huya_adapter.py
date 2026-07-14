import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.huya import HuyaAdapter
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
            anchor_name="huya-anchor",
            title="huya-title",
            is_live=True,
            streams=(
                ResolverStream("https://pull.example/huya.m3u8", "hls", "1080p", 1080),
                ResolverStream("https://pull.example/huya.flv", "flv", "1080p", 1080),
            ),
        )


class HuyaAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_only_supported_huya_hosts(self):
        adapter = HuyaAdapter(FakeBackend())

        for url in (
            "https://huya.com/123",
            "https://www.huya.com/123",
            "https://m.huya.com/123",
        ):
            with self.subTest(url=url):
                self.assertTrue(adapter.matches(url))

        self.assertFalse(adapter.matches("https://live.huya.com/123"))
        self.assertFalse(adapter.matches("https://example.com/www.huya.com/123"))
        self.assertFalse(adapter.matches("https:///missing-host"))

    async def test_resolves_with_platform_context_and_prefers_flv(self):
        backend = FakeBackend()
        adapter = HuyaAdapter(backend)
        target = RecordingTarget(url="https://www.huya.com/123")
        context = ResolveContext(
            quality=Quality.HIGH,
            proxy_addr="http://proxy.example:8080",
            cookies={"huya": "udb_uid=huya"},
        )

        stream = await adapter.resolve(target, context)

        self.assertEqual(stream.platform_key, "huya")
        self.assertEqual(stream.platform_name, "Huya")
        self.assertEqual(stream.anchor_name, "huya-anchor")
        self.assertEqual(stream.title, "huya-title")
        self.assertTrue(stream.is_live)
        self.assertEqual(stream.quality, Quality.HIGH)
        self.assertEqual(stream.primary_url, "https://pull.example/huya.flv")
        self.assertEqual(stream.flv_url, "https://pull.example/huya.flv")
        self.assertEqual(stream.hls_url, "https://pull.example/huya.m3u8")
        self.assertEqual(stream.headers["Referer"], "https://www.huya.com/")
        self.assertEqual(
            backend.calls,
            [
                (
                    target.url,
                    "http://proxy.example:8080",
                    "udb_uid=huya",
                    {"Referer": "https://www.huya.com/"},
                )
            ],
        )

    async def test_offline_result_with_no_streams_returns_offline(self):
        adapter = HuyaAdapter(
            FakeBackend(
                ResolverResult(
                    anchor_name="huya-anchor", title="offline", is_live=False
                )
            )
        )

        stream = await adapter.resolve(
            RecordingTarget(url="https://www.huya.com/123"), ResolveContext()
        )

        self.assertFalse(stream.is_live)
        self.assertEqual(stream.primary_url, "")
        self.assertEqual(stream.flv_url, "")
        self.assertEqual(stream.hls_url, "")


if __name__ == "__main__":
    unittest.main()
