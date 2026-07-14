import unittest

from lubo.core.models import Quality, RecordingTarget
from lubo.platforms.base import ResolveContext
from lubo.platforms.douyin import DouyinAdapter


async def fake_web_fetch(url: str, proxy_addr: str, cookies: str):
    return {"status": 2, "anchor_name": "主播A", "title": "直播标题", "source": "web"}


async def fake_app_fetch(url: str, proxy_addr: str, cookies: str):
    return {"status": 2, "anchor_name": "主播B", "title": "直播标题", "source": "app"}


async def fake_stream_resolve(json_data: dict, video_quality: str, proxy_addr: str):
    return {
        "anchor_name": json_data["anchor_name"],
        "is_live": True,
        "title": json_data["title"],
        "quality": video_quality,
        "record_url": "https://pull.example/live.m3u8",
        "flv_url": "https://pull.example/live.flv",
        "m3u8_url": "https://pull.example/live.m3u8",
    }


class FetchRecorder:
    def __init__(self, source: str):
        self.source = source
        self.calls = []

    async def __call__(self, url: str, proxy_addr: str, cookies: str):
        self.calls.append({"url": url, "proxy_addr": proxy_addr, "cookies": cookies})
        return {"status": 2, "anchor_name": self.source, "title": "title", "source": self.source}


class StreamRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, json_data: dict, video_quality: str, proxy_addr: str):
        self.calls.append({"json_data": json_data, "video_quality": video_quality, "proxy_addr": proxy_addr})
        return {
            "anchor_name": json_data["anchor_name"],
            "is_live": True,
            "title": json_data["title"],
            "record_url": "https://pull.example/live.m3u8",
        }


class DouyinAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_supported_hosts(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)

        self.assertTrue(adapter.matches("https://live.douyin.com/123"))
        self.assertTrue(adapter.matches("https://live.douyin.com:443/123"))
        self.assertTrue(adapter.matches("https://live.douyin.com"))
        self.assertTrue(adapter.matches("live.douyin.com"))
        self.assertTrue(adapter.matches("https://v.douyin.com/abc"))
        self.assertTrue(adapter.matches("https://v.douyin.com"))
        self.assertTrue(adapter.matches("v.douyin.com"))
        self.assertTrue(adapter.matches("https://www.douyin.com/user/example"))
        self.assertTrue(adapter.matches("https://www.douyin.com"))
        self.assertTrue(adapter.matches("www.douyin.com"))
        self.assertFalse(adapter.matches("https://live.bilibili.com/1"))
        self.assertFalse(adapter.matches("https://example.com/live.douyin.com/123"))

    async def test_resolves_web_live_room(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)
        target = RecordingTarget(url="https://live.douyin.com/123", quality=Quality.HIGH)

        info = await adapter.resolve(
            target,
            ResolveContext(quality=Quality.HIGH, proxy_addr="127.0.0.1:7890", cookies={"douyin": "cookie"}),
        )

        self.assertTrue(info.is_live)
        self.assertEqual(info.platform_key, "douyin")
        self.assertEqual(info.anchor_name, "主播A")
        self.assertEqual(info.primary_url, "https://pull.example/live.m3u8")
        self.assertEqual(info.flv_url, "https://pull.example/live.flv")

    async def test_resolves_share_link_with_app_fetcher(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)
        target = RecordingTarget(url="https://V.DOUYIN.COM/abc")

        info = await adapter.resolve(target, ResolveContext())

        self.assertEqual(info.anchor_name, "主播B")

    async def test_resolves_user_link_with_app_fetcher(self):
        web_fetcher = FetchRecorder("web")
        app_fetcher = FetchRecorder("app")
        adapter = DouyinAdapter(web_fetcher, app_fetcher, fake_stream_resolve)
        target = RecordingTarget(url="https://www.douyin.com/USER/example")

        await adapter.resolve(target, ResolveContext())

        self.assertEqual(web_fetcher.calls, [])
        self.assertEqual(app_fetcher.calls[0]["url"], target.url)

    async def test_resolve_passes_proxy_cookie_and_quality_to_fetchers(self):
        web_fetcher = FetchRecorder("web")
        app_fetcher = FetchRecorder("app")
        stream_resolver = StreamRecorder()
        adapter = DouyinAdapter(web_fetcher, app_fetcher, stream_resolver)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await adapter.resolve(
            target,
            ResolveContext(quality=Quality.ULTRA, proxy_addr="127.0.0.1:7890", cookies={"douyin": "session=abc"}),
        )

        self.assertEqual(web_fetcher.calls, [{"url": target.url, "proxy_addr": "127.0.0.1:7890", "cookies": "session=abc"}])
        self.assertEqual(app_fetcher.calls, [])
        self.assertEqual(stream_resolver.calls[0]["video_quality"], "UHD")
        self.assertEqual(stream_resolver.calls[0]["proxy_addr"], "127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
