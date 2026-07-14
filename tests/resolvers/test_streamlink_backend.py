import threading
import unittest

from lubo.resolvers.base import PlatformAccessError
from lubo.resolvers.streamlink_backend import StreamlinkBackend


class FakeStream:
    def __init__(self, url: str):
        self.url = url

    def to_url(self) -> str:
        return self.url


class FakePlugin:
    def __init__(self, streams, metadata):
        self._streams = streams
        self._metadata = metadata

    def streams(self):
        if isinstance(self._streams, Exception):
            raise self._streams
        return self._streams

    def get_metadata(self):
        return self._metadata


class FakeHttp:
    def __init__(self):
        self.headers = {}
        self.cookies = {}


class FakeSession:
    def __init__(self, plugin):
        self.plugin = plugin
        self.http = FakeHttp()
        self.options = {}
        self.resolved_url = None

    def set_option(self, name, value):
        self.options[name] = value

    def resolve_url(self, url):
        self.resolved_url = url
        return self.plugin


class SessionFactory:
    def __init__(self, session):
        self.session = session
        self.thread_id = None

    def __call__(self):
        self.thread_id = threading.get_ident()
        return self.session


class StreamlinkBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_maps_live_streams_metadata_and_session_options(self):
        plugin = FakePlugin(
            {
                "1080p_alt": FakeStream("https://cdn.example/live.flv?token=ok"),
                "best": FakeStream("https://cdn.example/live.m3u8"),
            },
            {"author": "Anchor Name", "title": "Live Title"},
        )
        session = FakeSession(plugin)
        factory = SessionFactory(session)
        main_thread_id = threading.get_ident()

        result = await StreamlinkBackend(factory).resolve(
            "https://live.example/room",
            proxy_addr="http://127.0.0.1:7890",
            cookies="session=session-value; theme=dark",
            headers={"Referer": "https://live.example"},
        )

        self.assertTrue(result.is_live)
        self.assertEqual(result.anchor_name, "Anchor Name")
        self.assertEqual(result.title, "Live Title")
        self.assertEqual(result.streams[0].protocol, "flv")
        self.assertEqual(result.streams[0].height, 1080)
        self.assertEqual(result.streams[1].protocol, "hls")
        self.assertEqual(session.options["http-proxy"], "http://127.0.0.1:7890")
        self.assertEqual(session.http.headers["Referer"], "https://live.example")
        self.assertEqual(session.http.cookies["session"], "session-value")
        self.assertEqual(session.http.cookies["theme"], "dark")
        self.assertEqual(session.resolved_url, "https://live.example/room")
        self.assertNotEqual(factory.thread_id, main_thread_id)

    async def test_resolve_returns_offline_result_when_no_streams_exist(self):
        plugin = FakePlugin({}, {"author": "Anchor", "title": "Ended"})

        result = await StreamlinkBackend(SessionFactory(FakeSession(plugin))).resolve(
            "https://live.example/offline"
        )

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "Anchor")
        self.assertEqual(result.title, "Ended")
        self.assertEqual(result.streams, ())

    async def test_resolve_redacts_engine_errors(self):
        source_url = "https://live.example/room?signature=url-secret"
        cookie_secret = "cookie-secret"
        plugin = FakePlugin(
            RuntimeError(
                "session-secret at https://cdn.example/live.flv?signature=engine-secret"
            ),
            None,
        )

        with self.assertRaises(PlatformAccessError) as raised:
            await StreamlinkBackend(SessionFactory(FakeSession(plugin))).resolve(
                source_url,
                cookies=f"session={cookie_secret}",
            )

        public_message = str(raised.exception)
        self.assertNotIn(source_url, public_message)
        self.assertNotIn(cookie_secret, public_message)
        self.assertNotIn("session-secret", public_message)
        self.assertNotIn("engine-secret", public_message)


if __name__ == "__main__":
    unittest.main()
