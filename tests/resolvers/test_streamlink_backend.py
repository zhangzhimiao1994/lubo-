import threading
import unittest

from lubo.resolvers.base import PlatformAccessError
from lubo.resolvers.streamlink_backend import StreamlinkBackend


class FakeStream:
    def __init__(self, url: str):
        self.url = url


class FakePlugin:
    def __init__(self, session, resolved_url):
        self.session = session
        self.resolved_url = resolved_url
        self.author = session.author
        self.title = session.title
        session.plugin_instances.append(self)

    def streams(self):
        if isinstance(self.session.streams, Exception):
            raise self.session.streams
        return self.session.streams


class FakeHttp:
    def __init__(self):
        self.headers = {}
        self.cookies = {}


class FakeSession:
    def __init__(
        self,
        streams,
        *,
        author="",
        title="",
        resolved_url="https://resolved.example/room",
    ):
        self.streams = streams
        self.author = author
        self.title = title
        self.plugin_instances = []
        self.http = FakeHttp()
        self.options = {}
        self.requested_url = None
        self.resolved_url = None
        self.plugin_resolved_url = resolved_url

    def set_option(self, name, value):
        self.options[name] = value

    def resolve_url(self, url):
        self.requested_url = url
        return "fake-plugin", FakePlugin, self.plugin_resolved_url


class SessionFactory:
    def __init__(self, session):
        self.session = session
        self.thread_id = None

    def __call__(self):
        self.thread_id = threading.get_ident()
        return self.session


class StreamlinkBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_maps_live_streams_metadata_and_session_options(self):
        session = FakeSession(
            {
                "1080p_alt": FakeStream("https://cdn.example/live.flv?token=ok"),
                "best": FakeStream("https://cdn.example/live.m3u8"),
            },
            author="Anchor Name",
            title="Live Title",
        )
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
        self.assertEqual(session.requested_url, "https://live.example/room")
        self.assertEqual(len(session.plugin_instances), 1)
        self.assertIs(session.plugin_instances[0].session, session)
        self.assertEqual(
            session.plugin_instances[0].resolved_url,
            "https://resolved.example/room",
        )
        self.assertNotEqual(factory.thread_id, main_thread_id)

    async def test_resolve_preserves_safe_streamlink_request_headers(self):
        session = FakeSession(
            {"best": FakeStream("https://cdn.example/live.flv")}
        )
        session.http.headers.update(
            {
                "User-Agent": "Streamlink Agent",
                "Origin": "https://www.huya.com",
                "Authorization": "must-not-leak",
            }
        )

        result = await StreamlinkBackend(SessionFactory(session)).resolve(
            "https://www.huya.com/123",
            headers={"Referer": "https://www.huya.com/"},
        )

        self.assertEqual(
            dict(result.streams[0].headers),
            {
                "User-Agent": "Streamlink Agent",
                "Origin": "https://www.huya.com",
                "Referer": "https://www.huya.com/",
            },
        )

    async def test_resolve_returns_offline_result_when_no_streams_exist(self):
        session = FakeSession({}, author="Anchor", title="Ended")

        result = await StreamlinkBackend(SessionFactory(session)).resolve(
            "https://live.example/offline"
        )

        self.assertFalse(result.is_live)
        self.assertEqual(result.anchor_name, "Anchor")
        self.assertEqual(result.title, "Ended")
        self.assertEqual(result.streams, ())

    async def test_resolve_keeps_http_streams_and_filters_non_http_streams(self):
        session = FakeSession(
            {
                "source": FakeStream("https://cdn.example/live.mp4"),
                "legacy": FakeStream("rtmp://cdn.example/live"),
            },
            author="Anchor",
            title="Live",
        )

        result = await StreamlinkBackend(SessionFactory(session)).resolve(
            "https://live.example/room"
        )

        self.assertEqual(len(result.streams), 1)
        self.assertEqual(result.streams[0].url, "https://cdn.example/live.mp4")
        self.assertEqual(result.streams[0].protocol, "http")

    async def test_resolve_redacts_engine_errors(self):
        source_url = "https://live.example/room?signature=url-secret"
        cookie_secret = "cookie-secret"
        session = FakeSession(
            RuntimeError(
                "session-secret at https://cdn.example/live.flv?signature=engine-secret"
            )
        )

        with self.assertRaises(PlatformAccessError) as raised:
            await StreamlinkBackend(SessionFactory(session)).resolve(
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
