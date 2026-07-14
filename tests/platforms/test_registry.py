import unittest

from douyinliverecorder.core.models import RecordingTarget, StreamInfo
from douyinliverecorder.platforms.base import PlatformAdapter, ResolveContext
from douyinliverecorder.platforms.registry import PlatformRegistry


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


if __name__ == "__main__":
    unittest.main()
