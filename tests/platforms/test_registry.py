import unittest
from unittest.mock import patch

from lubo.core.models import RecordingTarget, StreamInfo
from lubo.platforms.base import PlatformAdapter, ResolveContext
from lubo.platforms.factory import build_default_registry
from lubo.platforms.registry import PlatformRegistry


class FakeAdapter:
    key = "fake"
    display_name = "Fake"

    def matches(self, url: str) -> bool:
        return "example.com" in url

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        return StreamInfo(platform_key=self.key, platform_name=self.display_name, anchor_name="fake")


class RegistryTests(unittest.TestCase):
    def test_returns_matching_adapter(self):
        registry = PlatformRegistry([FakeAdapter()])
        adapter = registry.match("https://example.com/live")

        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.key, "fake")

    def test_returns_none_for_unknown_url(self):
        registry = PlatformRegistry([FakeAdapter()])

        self.assertIsNone(registry.match("https://unknown.invalid/live"))

    def test_protocol_shape_accepts_fake_adapter(self):
        adapter: PlatformAdapter = FakeAdapter()

        self.assertTrue(adapter.matches("https://example.com/live"))

    def test_resolve_context_cookies_are_defensively_copied(self):
        original = {"douyin": "a"}

        context = ResolveContext(cookies=original)
        original["douyin"] = "b"

        self.assertEqual(context.cookie_value("douyin"), "a")

    def test_resolve_context_cookies_mapping_is_immutable(self):
        context = ResolveContext(cookies={"douyin": "a"})

        with self.assertRaises(TypeError):
            context.cookies["douyin"] = "b"

    def test_constructor_isolates_caller_adapter_list(self):
        adapters = [FakeAdapter()]
        registry = PlatformRegistry(adapters)

        adapters.clear()

        self.assertIsNotNone(registry.match("https://example.com/live"))

    def test_default_factory_uses_injected_backends_in_required_order(self):
        class FalsyBackend:
            def __bool__(self):
                return False

        streamlink_backend = FalsyBackend()
        yt_dlp_backend = FalsyBackend()

        registry = build_default_registry(
            streamlink_backend=streamlink_backend,
            yt_dlp_backend=yt_dlp_backend,
        )

        self.assertEqual(
            [adapter.key for adapter in registry.adapters],
            ["douyin", "bilibili", "huya", "douyu"],
        )
        self.assertTrue(
            all(
                adapter.backend is streamlink_backend
                for adapter in registry.adapters[:3]
            )
        )
        self.assertIs(registry.adapters[3].backend, yt_dlp_backend)

    @patch("lubo.platforms.factory.YtDlpBackend")
    @patch("lubo.platforms.factory.StreamlinkBackend")
    def test_default_factory_instantiates_each_backend_once(
        self, streamlink_backend_class, yt_dlp_backend_class
    ):
        streamlink_backend = streamlink_backend_class.return_value
        yt_dlp_backend = yt_dlp_backend_class.return_value

        registry = build_default_registry()

        streamlink_backend_class.assert_called_once_with()
        yt_dlp_backend_class.assert_called_once_with()
        self.assertTrue(
            all(
                adapter.backend is streamlink_backend
                for adapter in registry.adapters[:3]
            )
        )
        self.assertIs(registry.adapters[3].backend, yt_dlp_backend)

    def test_default_adapters_reject_parser_confusion_urls(self):
        registry = build_default_registry(
            streamlink_backend=object(), yt_dlp_backend=object()
        )
        domains = {
            "douyin": "live.douyin.com",
            "bilibili": "live.bilibili.com",
            "huya": "www.huya.com",
            "douyu": "www.douyu.com",
        }

        for adapter in registry.adapters:
            domain = domains[adapter.key]
            with self.subTest(platform=adapter.key, case="normalized"):
                self.assertTrue(adapter.matches(f"{domain}/123"))
            invalid_urls = (
                f"javascript://{domain}/123",
                f"ftp://{domain}/123",
                f"https://{domain}:bad/123",
                f"https://example.com\\@{domain}/123",
                f"https://user@{domain}/123",
                f"https://user:password@{domain}/123",
            )
            for url in invalid_urls:
                with self.subTest(platform=adapter.key, url=url):
                    self.assertFalse(adapter.matches(url))


if __name__ == "__main__":
    unittest.main()
