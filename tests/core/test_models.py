import unittest

from lubo.core.models import (
    OutputFormat,
    Quality,
    RecordingStatus,
    RecordingTarget,
    StreamInfo,
    normalize_url,
)


class RecordingModelTests(unittest.TestCase):
    def test_recording_target_normalizes_url_and_defaults(self):
        target = RecordingTarget(url=" live.douyin.com/123456 ")

        self.assertEqual(target.url, "https://live.douyin.com/123456")
        self.assertTrue(target.enabled)
        self.assertEqual(target.quality, Quality.ORIGINAL)
        self.assertEqual(target.display_name, "")
        self.assertTrue(target.id)

    def test_normalize_url_trims_qualified_url(self):
        self.assertEqual(
            normalize_url(" https://live.douyin.com/123 "),
            "https://live.douyin.com/123",
        )

    def test_normalize_url_keeps_empty_url_empty(self):
        self.assertEqual(normalize_url(""), "")

    def test_normalize_url_prefixes_unqualified_url_with_scheme_in_query(self):
        self.assertEqual(
            normalize_url("live.douyin.com/123?redirect=https://example.com"),
            "https://live.douyin.com/123?redirect=https://example.com",
        )

    def test_normalize_url_prefixes_bare_host_with_port(self):
        self.assertEqual(
            normalize_url("live.douyin.com:443/123"),
            "https://live.douyin.com:443/123",
        )

    def test_normalize_url_trims_qualified_host_with_port(self):
        self.assertEqual(
            normalize_url(" https://live.douyin.com:443/123 "),
            "https://live.douyin.com:443/123",
        )

    def test_stream_info_identifies_not_live_without_url(self):
        info = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="alice")

        self.assertFalse(info.is_live)
        self.assertEqual(info.primary_url, "")

    def test_stream_info_accepts_recording_urls(self):
        info = StreamInfo(
            platform_key="douyin",
            platform_name="Douyin",
            anchor_name="alice",
            title="test",
            is_live=True,
            quality=Quality.ORIGINAL,
            primary_url="https://example.com/live.m3u8",
            flv_url="https://example.com/live.flv",
            hls_url="https://example.com/live.m3u8",
            headers={"referer": "https://live.douyin.com"},
        )

        self.assertTrue(info.is_live)
        self.assertEqual(info.primary_url, "https://example.com/live.m3u8")
        self.assertEqual(info.headers["referer"], "https://live.douyin.com")

    def test_stream_info_copies_headers(self):
        headers = {"referer": "https://live.douyin.com"}
        info = StreamInfo(platform_key="douyin", platform_name="Douyin", headers=headers)

        headers["referer"] = "https://example.com"

        self.assertEqual(info.headers["referer"], "https://live.douyin.com")

    def test_stream_info_headers_are_immutable(self):
        info = StreamInfo(
            platform_key="douyin",
            platform_name="Douyin",
            headers={"referer": "https://live.douyin.com"},
        )

        with self.assertRaises(TypeError):
            info.headers["referer"] = "https://example.com"

    def test_enums_use_existing_config_values(self):
        self.assertEqual(Quality.ORIGINAL.value, "原画")
        self.assertEqual(OutputFormat.TS.value, "ts")
        self.assertEqual(RecordingStatus.IDLE.value, "idle")


if __name__ == "__main__":
    unittest.main()
