import json
import unittest
from unittest.mock import patch
from urllib.request import Request

from lubo.resolvers.base import PlatformAccessError
from lubo.resolvers.douyin_web_backend import (
    DEFAULT_USER_AGENT,
    DouyinWebBackend,
    _SafeDouyinRedirectHandler,
    _fetch_page,
)


def pace_page(room):
    state = {"state": {"roomStore": {"roomInfo": room}}}
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    pushed = json.dumps(payload, ensure_ascii=False)
    return f'<html><script>self.__pace_f.push([1,{pushed}])</script></html>'


def live_room():
    return {
        "room": {
            "status": 2,
            "title": "Current Douyin live",
            "stream_url": {
                "flv_pull_url": {
                    "FULL_HD1": "http://flv.example/origin.flv?token=1",
                    "HD1": "https://flv.example/hd.flv",
                    "SD1": "ftp://flv.example/rejected.flv",
                    "SD2": "https://flv.example/ld.flv",
                    "UNKNOWN": "https://flv.example/unknown.flv",
                },
                "hls_pull_url_map": {
                    "FULL_HD1": "http://hls.example/origin.m3u8?token=2",
                    "HD1": "https://hls.example/hd.m3u8",
                    "SD1": "https://hls.example/sd.m3u8",
                    "SD2": "javascript:alert(1)",
                },
                "live_core_sdk_data": {
                    "pull_data": {
                        "options": {
                            "qualities": [
                                {"name": "蓝光", "sdk_key": "origin", "resolution": "1088x1920"},
                                {"name": "超清", "sdk_key": "hd", "resolution": "720x1270"},
                                {"name": "高清", "sdk_key": "sd", "resolution": "540x952"},
                                {"name": "标清", "sdk_key": "ld", "resolution": "480x847"},
                            ]
                        }
                    }
                },
            },
        },
        "anchor": {"nickname": "Desktop anchor"},
    }


class RecordingFetcher:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def __call__(
        self,
        url,
        *,
        headers,
        proxy_addr,
        timeout,
        max_response_bytes,
    ):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "proxy_addr": proxy_addr,
                "timeout": timeout,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self.page


class DouyinWebBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_latest_state_for_the_target_room(self):
        stale_live = live_room()
        stale_live["web_rid"] = "445365761510"
        stale_live["room"]["title"] = "stale target live state"
        current_offline = live_room()
        current_offline["web_rid"] = "445365761510"
        current_offline["room"]["status"] = 4
        current_offline["room"]["title"] = "Latest target offline state"
        backend = DouyinWebBackend(
            fetcher=RecordingFetcher(
                f"{pace_page(stale_live)}{pace_page(current_offline)}"
            )
        )

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertFalse(result.is_live)
        self.assertEqual(result.title, "Latest target offline state")

    async def test_prefers_target_offline_state_over_other_room_live_state(self):
        stale_live = live_room()
        stale_live["web_rid"] = "999999999999"
        stale_live["room"]["title"] = "stale live from another room"
        current_offline = live_room()
        current_offline["web_rid"] = "445365761510"
        current_offline["room"]["status"] = 4
        current_offline["room"]["title"] = "Current offline room"
        backend = DouyinWebBackend(
            fetcher=RecordingFetcher(
                f"{pace_page(stale_live)}{pace_page(current_offline)}"
            )
        )

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertFalse(result.is_live)
        self.assertEqual(result.title, "Current offline room")

    async def test_skips_empty_room_before_real_live_state(self):
        backend = DouyinWebBackend(
            fetcher=RecordingFetcher(
                f"{pace_page({'room': {}})}{pace_page(live_room())}"
            )
        )

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "Desktop anchor")

    async def test_prefers_live_state_over_earlier_offline_placeholder(self):
        offline = live_room()
        offline["room"]["status"] = 4
        offline["room"]["title"] = "stale offline placeholder"
        backend = DouyinWebBackend(
            fetcher=RecordingFetcher(
                f"{pace_page(offline)}{pace_page(live_room())}"
            )
        )

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertTrue(result.is_live)
        self.assertEqual(result.title, "Current Douyin live")

    async def test_collects_candidates_from_one_pace_text_chunk(self):
        offline = live_room()
        offline["room"]["status"] = 4
        offline_state = {"state": {"roomStore": {"roomInfo": offline}}}
        live_state = {"state": {"roomStore": {"roomInfo": live_room()}}}
        payload = json.dumps(offline_state) + "\n" + json.dumps(live_state)
        page = (
            "<script>self.__pace_f.push([1,"
            f"{json.dumps(payload)}"
            "])</script>"
        )
        backend = DouyinWebBackend(fetcher=RecordingFetcher(page))

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertTrue(result.is_live)
        self.assertEqual(result.title, "Current Douyin live")

    async def test_skips_empty_initial_room_store_before_real_room_state(self):
        empty_state = pace_page({})
        real_state = pace_page(live_room())
        backend = DouyinWebBackend(
            fetcher=RecordingFetcher(f"{empty_state}{real_state}")
        )

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "Desktop anchor")

    async def test_parses_current_pace_state_and_maps_quality_pairs(self):
        backend = DouyinWebBackend(fetcher=RecordingFetcher(pace_page(live_room())))

        result = await backend.resolve("https://live.douyin.com/445365761510")

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "Desktop anchor")
        self.assertEqual(result.title, "Current Douyin live")
        self.assertEqual(
            [
                (stream.quality_name, stream.protocol, stream.height, stream.url)
                for stream in result.streams
            ],
            [
                ("origin", "flv", 1088, "https://flv.example/origin.flv?token=1"),
                ("origin", "hls", 1088, "https://hls.example/origin.m3u8?token=2"),
                ("hd", "flv", 720, "https://flv.example/hd.flv"),
                ("hd", "hls", 720, "https://hls.example/hd.m3u8"),
                ("sd", "hls", 540, "https://hls.example/sd.m3u8"),
                ("ld", "flv", 480, "https://flv.example/ld.flv"),
                ("unknown", "flv", None, "https://flv.example/unknown.flv"),
            ],
        )
        for stream in result.streams:
            self.assertEqual(stream.headers, {"User-Agent": DEFAULT_USER_AGENT})

    async def test_offline_room_preserves_metadata_without_streams(self):
        room_info = live_room()
        room_info["room"]["status"] = 4
        backend = DouyinWebBackend(fetcher=RecordingFetcher(pace_page(room_info)))

        result = await backend.resolve("https://live.douyin.com/123")

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "Desktop anchor")
        self.assertEqual(result.title, "Current Douyin live")
        self.assertEqual(result.streams, ())

    async def test_live_room_with_missing_streams_is_deterministic(self):
        room_info = live_room()
        room_info["room"]["stream_url"] = None
        backend = DouyinWebBackend(fetcher=RecordingFetcher(pace_page(room_info)))

        result = await backend.resolve("https://live.douyin.com/123")

        self.assertTrue(result.is_live)
        self.assertEqual(result.streams, ())

    async def test_malformed_or_missing_room_state_is_sanitized(self):
        for page in (
            "<html>no state</html>",
            '<script>self.__pace_f.push([1,"not-json"])</script>',
            pace_page({"unexpected": True}),
        ):
            with self.subTest(page=page):
                backend = DouyinWebBackend(fetcher=RecordingFetcher(page))
                with self.assertRaisesRegex(
                    PlatformAccessError, "Douyin page could not be parsed"
                ):
                    await backend.resolve("https://live.douyin.com/123")

    async def test_fetcher_receives_cookie_proxy_custom_headers_and_limits(self):
        fetcher = RecordingFetcher(pace_page(live_room()))
        backend = DouyinWebBackend(
            fetcher=fetcher,
            timeout=7.5,
            max_response_bytes=123456,
        )

        await backend.resolve(
            "https://live.douyin.com/123",
            proxy_addr="http://127.0.0.1:7890",
            cookies="sessionid=secret; __ac_nonce=nonce-value",
            headers={"Referer": "https://custom.example/", "X-Test": "yes"},
        )

        self.assertEqual(
            fetcher.calls,
            [
                {
                    "url": "https://live.douyin.com/123",
                    "headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": "https://custom.example/",
                        "X-Test": "yes",
                        "Cookie": "sessionid=secret; __ac_nonce=nonce-value",
                    },
                    "proxy_addr": "http://127.0.0.1:7890",
                    "timeout": 7.5,
                    "max_response_bytes": 123456,
                }
            ],
        )
        self.assertNotIn("Cookie", (await backend.resolve(
            "https://live.douyin.com/123", cookies="sessionid=secret"
        )).streams[0].headers)

    async def test_fetch_errors_do_not_expose_credentials_or_transport_details(self):
        def failing_fetcher(*args, **kwargs):
            raise OSError("proxy password and internal transport detail")

        backend = DouyinWebBackend(fetcher=failing_fetcher)

        with self.assertRaisesRegex(
            PlatformAccessError, "Douyin page could not be accessed"
        ) as caught:
            await backend.resolve(
                "https://live.douyin.com/123",
                proxy_addr="http://user:proxy-password@127.0.0.1:7890",
                cookies="sessionid=cookie-secret",
            )

        self.assertNotIn("password", str(caught.exception))
        self.assertNotIn("cookie-secret", str(caught.exception))

    async def test_unknown_quality_metadata_has_stable_safe_fallback(self):
        room_info = live_room()
        stream_url = room_info["room"]["stream_url"]
        stream_url["live_core_sdk_data"] = {"pull_data": {"options": {"qualities": []}}}
        stream_url["flv_pull_url"] = {
            "SD2": "https://cdn.example/ld.flv",
            "FULL_HD1": "https://cdn.example/origin.flv",
        }
        stream_url["hls_pull_url_map"] = {}
        backend = DouyinWebBackend(fetcher=RecordingFetcher(pace_page(room_info)))

        result = await backend.resolve("https://live.douyin.com/123")

        self.assertEqual(
            [(stream.quality_name, stream.height) for stream in result.streams],
            [("origin", None), ("ld", None)],
        )


class DefaultHttpFetcherTests(unittest.TestCase):
    @patch("lubo.resolvers.douyin_web_backend.build_opener")
    def test_default_fetcher_configures_both_proxy_schemes_and_size_limit(self, build_opener):
        response = build_opener.return_value.open.return_value.__enter__.return_value
        response.read.side_effect = [b"abc", b"d"]
        backend = DouyinWebBackend(max_response_bytes=3)

        with self.assertRaisesRegex(
            PlatformAccessError, "Douyin page could not be accessed"
        ):
            backend._resolve_sync(
                "https://live.douyin.com/123",
                proxy_addr="http://127.0.0.1:7890",
            )

        proxy_handler = build_opener.call_args.args[0]
        self.assertEqual(
            proxy_handler.proxies,
            {
                "http": "http://127.0.0.1:7890",
                "https": "http://127.0.0.1:7890",
            },
        )
        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), DEFAULT_USER_AGENT)
        self.assertEqual(request.get_header("Referer"), "https://live.douyin.com/")

    @patch("lubo.resolvers.douyin_web_backend.build_opener")
    def test_initial_http_url_is_normalized_to_trusted_https(self, build_opener):
        response = build_opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b""

        _fetch_page(
            "http://live.douyin.com/123?room=1",
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Host": "evil.example",
            },
            proxy_addr="",
            timeout=1,
            max_response_bytes=100,
        )

        request = build_opener.return_value.open.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://live.douyin.com/123?room=1",
        )
        self.assertIsNone(request.get_header("Host"))

    @patch("lubo.resolvers.douyin_web_backend.build_opener")
    def test_initial_request_rejects_untrusted_or_ambiguous_urls(self, build_opener):
        invalid_urls = (
            "ftp://live.douyin.com/123",
            "https://evil.example/123",
            "https://user@live.douyin.com/123",
            "https://live.douyin.com:444/123",
            "https://live.douyin.com.evil.example/123",
        )

        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                _fetch_page(
                    url,
                    headers={"Cookie": "sessionid=secret"},
                    proxy_addr="",
                    timeout=1,
                    max_response_bytes=100,
                )

        build_opener.assert_not_called()


class SafeDouyinRedirectHandlerTests(unittest.TestCase):
    def setUp(self):
        self.handler = _SafeDouyinRedirectHandler()
        self.request = Request(
            "https://live.douyin.com/123",
            headers={
                "Cookie": "sessionid=secret",
                "User-Agent": DEFAULT_USER_AGENT,
            },
        )

    def redirect(self, target):
        return self.handler.redirect_request(
            self.request,
            None,
            302,
            "Found",
            {},
            target,
        )

    def test_cross_host_redirect_drops_explicit_cookie(self):
        redirected = self.redirect("https://v.douyin.com/short-link")

        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Cookie"))
        self.assertEqual(
            redirected.get_header("User-agent"),
            DEFAULT_USER_AGENT,
        )

    def test_same_host_redirect_keeps_cookie(self):
        redirected = self.redirect("https://live.douyin.com/456")

        self.assertEqual(redirected.get_header("Cookie"), "sessionid=secret")

    def test_redirect_rejects_malicious_targets(self):
        invalid_targets = (
            "http://live.douyin.com/123",
            "file:///etc/passwd",
            "https://evil.example/123",
            "https://user@v.douyin.com/123",
            "https://v.douyin.com:444/123",
        )

        for target in invalid_targets:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.redirect(target)


if __name__ == "__main__":
    unittest.main()
