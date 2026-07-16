from __future__ import annotations

import threading
import traceback
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lubo.apps.desktop.pyav_decoder import DecodedFrame, PreviewDecodeError, PyAvDecoder
from lubo.core.models import StreamInfo


class FakePlane:
    def __init__(self, data: bytes, line_size: int) -> None:
        self._data = data
        self.line_size = line_size

    def __bytes__(self) -> bytes:
        return self._data


class FakeFrame:
    def __init__(
        self,
        width: int,
        height: int,
        marker: int = 1,
        *,
        padding: int = 0,
    ) -> None:
        self.width = width
        self.height = height
        self.marker = marker
        self.padding = padding
        self.reformat_calls: list[dict[str, object]] = []

    def reformat(self, **kwargs):
        self.reformat_calls.append(kwargs)
        width = kwargs["width"]
        height = kwargs["height"]
        row_size = width * 4
        line_size = row_size + self.padding
        rows = [
            bytes([(self.marker + row) % 256]) * row_size + b"P" * self.padding
            for row in range(height)
        ]
        return SimpleNamespace(
            width=width,
            height=height,
            planes=[FakePlane(b"".join(rows), line_size)],
        )


class FakePlaneFrame(FakeFrame):
    def __init__(self, width: int, height: int, plane: FakePlane) -> None:
        super().__init__(width, height)
        self.plane = plane

    def reformat(self, **kwargs):
        self.reformat_calls.append(kwargs)
        return SimpleNamespace(planes=[self.plane])


class FakeContainer:
    def __init__(
        self,
        frames=(),
        *,
        video_streams=None,
        decode_error=None,
        close_error=None,
    ) -> None:
        self.frames = list(frames)
        self.video_streams = [object()] if video_streams is None else video_streams
        self.audio_stream = object()
        self.streams = SimpleNamespace(
            video=self.video_streams,
            audio=[self.audio_stream],
        )
        self.decode_error = decode_error
        self.close_error = close_error
        self.decode_calls: list[object] = []
        self.close_calls = 0
        self.closed = False

    def decode(self, stream):
        self.decode_calls.append(stream)
        if self.decode_error is not None:
            raise self.decode_error

        def iterate():
            for frame in self.frames:
                if self.closed:
                    return
                yield frame

        return iterate()

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls > 1:
            raise RuntimeError("container cannot be closed twice")
        if self.close_error is not None:
            raise self.close_error
        self.closed = True


class FakeOpener:
    def __init__(self, containers) -> None:
        self.containers = list(containers)
        self.calls: list[tuple[object, dict[str, object]]] = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.containers.pop(0)


def stream(**overrides) -> StreamInfo:
    values = {
        "platform_key": "douyin",
        "platform_name": "Douyin",
        "primary_url": "https://pull.example/live.flv?token=signed-secret",
    }
    values.update(overrides)
    return StreamInfo(**values)


class PyAvDecoderTests(unittest.TestCase):
    def assert_error_is_sanitized(self, error, *secrets):
        formatted = "".join(traceback.format_exception(error))
        for secret in secrets:
            self.assertNotIn(secret, formatted)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_decoded_frame_is_immutable_and_slotted(self):
        frame = DecodedFrame(width=1, height=1, rgba=b"rgba")

        with self.assertRaises(FrozenInstanceError):
            frame.width = 2

        self.assertFalse(hasattr(frame, "__dict__"))

    def test_forwards_only_safe_headers_through_http_bridge(self):
        container = FakeContainer([FakeFrame(2, 1)])
        opener = FakeOpener([container])
        source = stream(
            headers={
                "user-AGENT": "desktop-agent",
                "REFERER": "https://www.douyin.com/",
                "origin": "https://www.douyin.com",
                "Cookie": "session-secret",
                "Authorization": "bearer-secret",
                "X-Unknown": "unknown-secret",
            }
        )

        frames = list(PyAvDecoder(open_container=opener).frames(source, threading.Event()))

        self.assertEqual(len(frames), 1)
        self.assertEqual(
            opener.calls,
            [
                (
                    source.primary_url,
                    {
                        "mode": "r",
                        "timeout": (10.0, 3.0),
                        "http_headers": {
                            "User-Agent": "desktop-agent",
                            "Referer": "https://www.douyin.com/",
                            "Origin": "https://www.douyin.com",
                        },
                    },
                )
            ],
        )

    def test_drops_crlf_injected_safe_header_values(self):
        container = FakeContainer([FakeFrame(1, 1)])
        opener = FakeOpener([container])
        source = stream(
            headers={
                "User-Agent": "agent\r\nCookie: injected-secret",
                "Referer": "https://safe.example/\nAuthorization: injected-secret",
                "Origin": "https://safe.example",
            }
        )

        list(PyAvDecoder(open_container=opener).frames(source, threading.Event()))

        headers = opener.calls[0][1]["http_headers"]
        self.assertEqual(headers, {"Origin": "https://safe.example"})
        self.assertNotIn("injected-secret", repr(headers))

    def test_hls_open_does_not_forward_http_options(self):
        opener = FakeOpener([FakeContainer([FakeFrame(1, 1)])])
        source = stream(
            primary_url="https://pull.example/live.m3u8",
            flv_url="",
            hls_url="https://pull.example/live.m3u8",
            headers={"Referer": "https://safe.example/"},
        )

        list(PyAvDecoder(open_container=opener).frames(source, threading.Event()))

        self.assertEqual(
            opener.calls,
            [
                (
                    source.primary_url,
                    {"mode": "r", "timeout": (10.0, 3.0)},
                )
            ],
        )

    def test_default_opener_owns_http_response_without_passing_av_options(self):
        response = Mock()
        inner_container = Mock()
        fake_av = SimpleNamespace(open=Mock(return_value=inner_container))
        headers = {
            "User-Agent": "desktop-agent",
            "Referer": "https://safe.example/",
        }

        with (
            patch.dict("sys.modules", {"av": fake_av}),
            patch(
                "lubo.apps.desktop.pyav_decoder.urllib.request.urlopen",
                return_value=response,
            ) as urlopen,
        ):
            container = PyAvDecoder._default_open_container(
                "https://pull.example/live.flv",
                mode="r",
                timeout=(10.0, 3.0),
                http_headers=headers,
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://pull.example/live.flv")
        self.assertEqual(request.get_header("User-agent"), "desktop-agent")
        self.assertEqual(request.get_header("Referer"), "https://safe.example/")
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 10.0})
        fake_av.open.assert_called_once_with(response, mode="r")

        container.close()

        response.close.assert_called_once_with()
        inner_container.close.assert_called_once_with()

    def test_uses_primary_then_flv_then_hls_url(self):
        cases = [
            ({"primary_url": "primary", "flv_url": "flv", "hls_url": "hls"}, "primary"),
            ({"primary_url": "", "flv_url": "flv", "hls_url": "hls"}, "flv"),
            ({"primary_url": " ", "flv_url": "", "hls_url": "hls"}, "hls"),
        ]

        for urls, expected in cases:
            with self.subTest(expected=expected):
                opener = FakeOpener([FakeContainer([FakeFrame(1, 1)])])
                list(PyAvDecoder(open_container=opener).frames(stream(**urls), threading.Event()))
                self.assertEqual(opener.calls[0][0], expected)

    def test_rejects_missing_url_without_exposing_headers(self):
        source = stream(
            primary_url="",
            flv_url="",
            hls_url="",
            headers={"Authorization": "header-secret"},
        )

        with self.assertRaises(PreviewDecodeError) as raised:
            list(PyAvDecoder(open_container=FakeOpener([])).frames(source, threading.Event()))

        self.assertEqual(str(raised.exception), "Preview stream has no usable URL.")
        self.assertNotIn("header-secret", repr(raised.exception))

    def test_rejects_missing_video_without_exposing_source(self):
        source = stream(headers={"Referer": "referer-secret"})
        container = FakeContainer(video_streams=[])

        with self.assertRaises(PreviewDecodeError) as raised:
            list(PyAvDecoder(open_container=FakeOpener([container])).frames(source, threading.Event()))

        self.assertEqual(str(raised.exception), "Preview stream has no video track.")
        self.assertNotIn("signed-secret", repr(raised.exception))
        self.assertNotIn("referer-secret", repr(raised.exception))
        self.assertEqual(container.close_calls, 1)

    def test_already_stopped_does_not_open(self):
        opener = FakeOpener([])
        stop_event = threading.Event()
        stop_event.set()

        self.assertEqual(
            list(PyAvDecoder(open_container=opener).frames(stream(), stop_event)),
            [],
        )
        self.assertEqual(opener.calls, [])

    def test_stop_during_open_closes_returned_container(self):
        container = FakeContainer([FakeFrame(1, 1)])
        stop_event = threading.Event()

        def opener(url, **kwargs):
            stop_event.set()
            return container

        decoded = list(PyAvDecoder(open_container=opener).frames(stream(), stop_event))

        self.assertEqual(decoded, [])
        self.assertEqual(container.close_calls, 1)

    def test_blocked_open_failure_becomes_cancellation_and_releases_decoder(self):
        for cancellation in ("close", "stop"):
            with self.subTest(cancellation=cancellation):
                entered = threading.Event()
                release = threading.Event()
                stop_event = threading.Event()
                reusable_container = FakeContainer([FakeFrame(1, 1)])
                calls = 0

                def opener(url, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        entered.set()
                        if not release.wait(2.0):
                            raise RuntimeError("blocked opener timed out")
                        raise RuntimeError(f"open-secret {url} {kwargs}")
                    return reusable_container

                decoder = PyAvDecoder(open_container=opener)
                frames = []
                errors = []

                def consume():
                    try:
                        frames.extend(decoder.frames(stream(), stop_event))
                    except Exception as error:
                        errors.append(error)

                worker = threading.Thread(target=consume)
                worker.start()
                try:
                    self.assertTrue(entered.wait(1.0))
                    if cancellation == "close":
                        decoder.close()
                    else:
                        stop_event.set()
                finally:
                    release.set()
                worker.join(2.0)

                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(frames, [])
                self.assertNotIn("signed-secret", repr(errors))
                self.assertNotIn("open-secret", repr(errors))
                reused = list(decoder.frames(stream(), threading.Event()))
                self.assertEqual(len(reused), 1)
                self.assertEqual(reusable_container.close_calls, 1)

    def test_blocked_video_lookup_failure_becomes_cancellation(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingStreams:
            @property
            def video(self):
                entered.set()
                if not release.wait(2.0):
                    raise RuntimeError("blocked lookup timed out")
                raise RuntimeError("lookup-secret signed-secret")

        blocked_container = FakeContainer()
        blocked_container.streams = BlockingStreams()
        reusable_container = FakeContainer([FakeFrame(1, 1)])
        decoder = PyAvDecoder(
            open_container=FakeOpener([blocked_container, reusable_container])
        )
        errors = []

        def consume():
            try:
                list(decoder.frames(stream(), threading.Event()))
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=consume)
        worker.start()
        try:
            self.assertTrue(entered.wait(1.0))
            decoder.close()
        finally:
            release.set()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(blocked_container.close_calls, 1)
        self.assertNotIn("lookup-secret", repr(errors))
        self.assertNotIn("signed-secret", repr(errors))
        self.assertEqual(
            len(list(decoder.frames(stream(), threading.Event()))),
            1,
        )
        self.assertEqual(reusable_container.close_calls, 1)

    def test_scales_to_fit_without_upscaling(self):
        cases = [
            ((1920, 1080), (1280, 720)),
            ((1080, 1920), (405, 720)),
            ((640, 360), (640, 360)),
        ]

        for source_size, expected in cases:
            with self.subTest(source_size=source_size):
                fake_frame = FakeFrame(*source_size)
                decoded = list(
                    PyAvDecoder(
                        open_container=FakeOpener([FakeContainer([fake_frame])])
                    ).frames(stream(), threading.Event())
                )

                self.assertEqual((decoded[0].width, decoded[0].height), expected)
                self.assertEqual(
                    fake_frame.reformat_calls,
                    [{"width": expected[0], "height": expected[1], "format": "rgba"}],
                )

    def test_preserves_odd_dimensions_without_upscaling(self):
        fake_frame = FakeFrame(1279, 719)

        decoded = list(
            PyAvDecoder(
                open_container=FakeOpener([FakeContainer([fake_frame])])
            ).frames(stream(), threading.Event())
        )[0]

        self.assertEqual((decoded.width, decoded.height), (1279, 719))

    def test_invalid_dimensions_are_generic_decode_errors(self):
        for width, height in ((0, 1), (1, 0), (-1, 1), (1, -1)):
            with self.subTest(width=width, height=height):
                container = FakeContainer([FakeFrame(width, height)])

                with self.assertRaisesRegex(
                    PreviewDecodeError, "Unable to decode preview stream"
                ):
                    list(
                        PyAvDecoder(open_container=FakeOpener([container])).frames(
                            stream(), threading.Event()
                        )
                    )

                self.assertEqual(container.close_calls, 1)

    def test_compacts_padded_plane_rows(self):
        fake_frame = FakeFrame(2, 2, marker=10, padding=5)

        decoded = list(
            PyAvDecoder(
                open_container=FakeOpener([FakeContainer([fake_frame])])
            ).frames(stream(), threading.Event())
        )[0]

        self.assertEqual(decoded.rgba, bytes([10]) * 8 + bytes([11]) * 8)
        self.assertEqual(len(decoded.rgba), decoded.width * decoded.height * 4)

    def test_rejects_negative_short_and_truncated_plane_layouts(self):
        cases = [
            FakePlane(b"x" * 16, -8),
            FakePlane(b"x" * 16, 7),
            FakePlane(b"x" * 8, 8),
        ]

        for plane in cases:
            with self.subTest(line_size=plane.line_size, length=len(bytes(plane))):
                container = FakeContainer([FakePlaneFrame(2, 2, plane)])

                with self.assertRaisesRegex(
                    PreviewDecodeError, "Unable to decode preview stream"
                ):
                    list(
                        PyAvDecoder(open_container=FakeOpener([container])).frames(
                            stream(), threading.Event()
                        )
                    )

                self.assertEqual(container.close_calls, 1)

    def test_limits_delivery_to_fifteen_frames_per_second(self):
        times = iter([0.0, 0.01, 0.07, 0.10])
        fake_frames = [FakeFrame(1, 1, marker=value) for value in range(1, 5)]

        decoded = list(
            PyAvDecoder(
                open_container=FakeOpener([FakeContainer(fake_frames)]),
                monotonic=lambda: next(times),
            ).frames(stream(), threading.Event())
        )

        self.assertEqual([frame.rgba[0] for frame in decoded], [1, 3])

    def test_delivers_frame_at_exact_fps_boundary(self):
        times = iter([0.0, 1.0 / 15.0])
        fake_frames = [FakeFrame(1, 1, marker=value) for value in (1, 2)]

        decoded = list(
            PyAvDecoder(
                open_container=FakeOpener([FakeContainer(fake_frames)]),
                monotonic=lambda: next(times),
            ).frames(stream(), threading.Event())
        )

        self.assertEqual([frame.rgba[0] for frame in decoded], [1, 2])

    def test_decodes_only_the_first_video_stream(self):
        first_video = object()
        second_video = object()
        container = FakeContainer(
            [FakeFrame(1, 1)], video_streams=[first_video, second_video]
        )

        list(PyAvDecoder(open_container=FakeOpener([container])).frames(stream(), threading.Event()))

        self.assertEqual(container.decode_calls, [first_video])
        self.assertNotIn(container.audio_stream, container.decode_calls)

    def test_stop_during_iteration_prevents_more_frames_and_closes(self):
        container = FakeContainer([FakeFrame(1, 1), FakeFrame(1, 1)])
        stop_event = threading.Event()
        iterator = PyAvDecoder(open_container=FakeOpener([container])).frames(
            stream(), stop_event
        )

        next(iterator)
        stop_event.set()

        self.assertEqual(list(iterator), [])
        self.assertEqual(container.close_calls, 1)

    def test_explicit_close_and_finally_close_container_exactly_once(self):
        explicit_container = FakeContainer([FakeFrame(1, 1), FakeFrame(1, 1)])
        decoder = PyAvDecoder(open_container=FakeOpener([explicit_container]))
        iterator = decoder.frames(stream(), threading.Event())
        next(iterator)

        decoder.close()
        list(iterator)

        self.assertEqual(explicit_container.close_calls, 1)

        normal_container = FakeContainer([FakeFrame(1, 1)])
        list(
            PyAvDecoder(open_container=FakeOpener([normal_container])).frames(
                stream(), threading.Event()
            )
        )
        self.assertEqual(normal_container.close_calls, 1)

    def test_concurrent_close_detaches_container_once(self):
        container = FakeContainer([FakeFrame(1, 1), FakeFrame(1, 1)])
        decoder = PyAvDecoder(open_container=FakeOpener([container]))
        iterator = decoder.frames(stream(), threading.Event())
        next(iterator)
        barrier = threading.Barrier(3)

        def close_decoder():
            barrier.wait()
            decoder.close()

        threads = [threading.Thread(target=close_decoder) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        list(iterator)

        self.assertEqual(container.close_calls, 1)

    def test_concurrent_failed_close_reports_to_only_one_caller(self):
        container = FakeContainer(
            [FakeFrame(1, 1), FakeFrame(1, 1)],
            close_error=RuntimeError("close-secret signed-secret"),
        )
        decoder = PyAvDecoder(open_container=FakeOpener([container]))
        iterator = decoder.frames(stream(), threading.Event())
        next(iterator)
        barrier = threading.Barrier(3)
        errors = []

        def close_decoder():
            barrier.wait()
            try:
                decoder.close()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=close_decoder) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2.0)
        iterator.close()

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(container.close_calls, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(str(errors[0]), "Unable to close preview stream.")
        self.assert_error_is_sanitized(errors[0], "close-secret", "signed-secret")

    def test_explicit_close_failure_is_generic_and_sanitized(self):
        container = FakeContainer(
            [FakeFrame(1, 1), FakeFrame(1, 1)],
            close_error=RuntimeError("close-secret signed-secret"),
        )
        decoder = PyAvDecoder(open_container=FakeOpener([container]))
        iterator = decoder.frames(stream(), threading.Event())
        next(iterator)

        with self.assertRaises(PreviewDecodeError) as raised:
            decoder.close()
        iterator.close()

        self.assertEqual(str(raised.exception), "Unable to close preview stream.")
        self.assertEqual(container.close_calls, 1)
        self.assert_error_is_sanitized(
            raised.exception,
            "close-secret",
            "signed-secret",
        )

    def test_cleanup_failure_logs_only_a_generic_warning(self):
        container = FakeContainer(
            [FakeFrame(1, 1)],
            close_error=RuntimeError("close-secret signed-secret"),
        )

        with self.assertLogs("lubo.apps.desktop.pyav_decoder", level="WARNING") as logs:
            decoded = list(
                PyAvDecoder(open_container=FakeOpener([container])).frames(
                    stream(), threading.Event()
                )
            )

        diagnostics = "\n".join(logs.output)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(container.close_calls, 1)
        self.assertIn("Unable to close preview stream during cleanup.", diagnostics)
        self.assertNotIn("close-secret", diagnostics)
        self.assertNotIn("signed-secret", diagnostics)
        self.assertNotIn(repr(container), diagnostics)

    def test_cleanup_failure_does_not_mask_decode_error(self):
        container = FakeContainer(
            decode_error=RuntimeError("decode-secret signed-secret"),
            close_error=RuntimeError("close-secret signed-secret"),
        )

        with self.assertLogs("lubo.apps.desktop.pyav_decoder", level="WARNING") as logs:
            with self.assertRaises(PreviewDecodeError) as raised:
                list(
                    PyAvDecoder(open_container=FakeOpener([container])).frames(
                        stream(), threading.Event()
                    )
                )

        diagnostics = "\n".join(logs.output)
        self.assertEqual(str(raised.exception), "Unable to decode preview stream.")
        self.assertEqual(container.close_calls, 1)
        self.assertNotIn("decode-secret", diagnostics)
        self.assertNotIn("close-secret", diagnostics)
        self.assert_error_is_sanitized(
            raised.exception,
            "decode-secret",
            "close-secret",
            "signed-secret",
        )

    def test_rejects_overlapping_iteration_and_releases_after_cleanup(self):
        first = FakeContainer([FakeFrame(1, 1), FakeFrame(1, 1)])
        second = FakeContainer([FakeFrame(1, 1)])
        opener = FakeOpener([first, second])
        decoder = PyAvDecoder(open_container=opener)
        first_iterator = decoder.frames(stream(), threading.Event())
        next(first_iterator)

        with self.assertRaisesRegex(PreviewDecodeError, "already active"):
            list(decoder.frames(stream(), threading.Event()))

        first_iterator.close()
        self.assertEqual(len(list(decoder.frames(stream(), threading.Event()))), 1)
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)

    def test_open_and_decode_errors_have_generic_safe_messages(self):
        source = stream(headers={"Origin": "origin-secret"})

        def failing_opener(url, **kwargs):
            raise RuntimeError(f"open failed for {url} with {kwargs}")

        with self.assertRaises(PreviewDecodeError) as open_error:
            list(PyAvDecoder(open_container=failing_opener).frames(source, threading.Event()))

        self.assertEqual(str(open_error.exception), "Unable to open preview stream.")
        self.assertNotIn("signed-secret", repr(open_error.exception))
        self.assertNotIn("origin-secret", repr(open_error.exception))
        self.assert_error_is_sanitized(
            open_error.exception,
            "signed-secret",
            "origin-secret",
        )

        container = FakeContainer(decode_error=RuntimeError("decode signed-secret origin-secret"))
        with self.assertRaises(PreviewDecodeError) as decode_error:
            list(PyAvDecoder(open_container=FakeOpener([container])).frames(source, threading.Event()))

        self.assertEqual(str(decode_error.exception), "Unable to decode preview stream.")
        self.assertNotIn("signed-secret", repr(decode_error.exception))
        self.assertNotIn("origin-secret", repr(decode_error.exception))
        self.assert_error_is_sanitized(
            decode_error.exception,
            "signed-secret",
            "origin-secret",
        )
        self.assertEqual(container.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
