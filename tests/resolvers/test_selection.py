import unittest

from lubo.core.models import Quality
from lubo.resolvers.base import NoCompatibleStreamError, ResolverStream
from lubo.resolvers.selection import select_stream


def stream(url: str, protocol: str, height: int | None) -> ResolverStream:
    return ResolverStream(url=url, protocol=protocol, height=height)


class StreamSelectionTests(unittest.TestCase):
    def test_original_selects_highest_height_and_prefers_flv_on_tie(self):
        streams = (
            stream("https://example.com/720.flv", "flv", 720),
            stream("https://example.com/1080.m3u8", "hls", 1080),
            stream("https://example.com/1080.flv", "flv", 1080),
        )

        selected = select_stream(streams, Quality.ORIGINAL)

        self.assertEqual(selected.url, "https://example.com/1080.flv")

    def test_standard_selects_720p_instead_of_1080p(self):
        streams = (
            stream("https://example.com/1080.flv", "flv", 1080),
            stream("https://example.com/720.m3u8", "hls", 720),
        )

        selected = select_stream(streams, Quality.STANDARD)

        self.assertEqual(selected.height, 720)

    def test_smooth_falls_back_to_lowest_stream_when_all_exceed_480p(self):
        streams = (
            stream("https://example.com/1080.flv", "flv", 1080),
            stream("https://example.com/720.m3u8", "hls", 720),
        )

        selected = select_stream(streams, Quality.SMOOTH)

        self.assertEqual(selected.height, 720)

    def test_empty_candidates_raise_no_compatible_stream_error(self):
        with self.assertRaises(NoCompatibleStreamError):
            select_stream((), Quality.ORIGINAL)

    def test_resolver_stream_copies_and_freezes_headers(self):
        headers = {"referer": "https://live.douyin.com"}
        candidate = ResolverStream(
            url="https://example.com/live.flv",
            protocol="flv",
            headers=headers,
        )

        headers["referer"] = "https://example.com"

        self.assertEqual(candidate.headers["referer"], "https://live.douyin.com")
        with self.assertRaises(TypeError):
            candidate.headers["referer"] = "https://example.com"


if __name__ == "__main__":
    unittest.main()
