import unittest

from lubo.platforms.engine import _protocol_url
from lubo.resolvers.base import ResolverStream


def stream(protocol, quality_name, height, suffix):
    return ResolverStream(
        url=f"https://cdn.example/{suffix}",
        protocol=protocol,
        quality_name=quality_name,
        height=height,
    )


class ProtocolPairingTests(unittest.TestCase):
    def test_pairs_normalized_quality_name_before_height(self):
        selected = stream("flv", " Origin ", 1080, "origin.flv")
        wrong_same_height = stream("hls", "hd", 1080, "hd.m3u8")
        same_quality_unknown_height = stream(
            "hls", "ORIGIN", None, "origin.m3u8"
        )

        result = _protocol_url(
            (selected, wrong_same_height, same_quality_unknown_height),
            "hls",
            selected,
        )

        self.assertEqual(result, same_quality_unknown_height.url)

    def test_does_not_pair_different_named_qualities_by_height(self):
        selected = stream("flv", "origin", 1080, "origin.flv")
        wrong_same_height = stream("hls", "hd", 1080, "hd.m3u8")

        result = _protocol_url(
            (selected, wrong_same_height),
            "hls",
            selected,
        )

        self.assertEqual(result, "")

    def test_height_fallback_allows_missing_quality_name(self):
        selected = stream("flv", "origin", 1080, "origin.flv")
        unnamed = stream("hls", "", 1080, "unnamed.m3u8")

        result = _protocol_url((selected, unnamed), "hls", selected)

        self.assertEqual(result, unnamed.url)


if __name__ == "__main__":
    unittest.main()
