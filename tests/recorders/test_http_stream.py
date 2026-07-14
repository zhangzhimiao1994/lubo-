import dataclasses
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from douyinliverecorder.core.models import RecordingTarget, StreamInfo
from douyinliverecorder.recorders.ffmpeg import RecorderOptions
from douyinliverecorder.recorders.http_stream import (
    DirectHttpRecorder,
    HTTPStreamCommand,
    HTTPStreamRecorder,
)


class FakeResponse:
    def __init__(self, chunks=(), error=None):
        self._chunks = iter(chunks)
        self._error = error
        self.closed = False
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        try:
            return next(self._chunks)
        except StopIteration:
            if self._error is not None:
                raise self._error
            return b""

    def close(self):
        self.closed = True


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.response


class HTTPStreamRecorderTests(unittest.TestCase):
    def live_stream(self, **overrides):
        values = {
            "platform_key": "douyin",
            "platform_name": "Douyin",
            "anchor_name": "Anchor A",
            "is_live": True,
            "primary_url": "https://pull.example/live.m3u8",
            "flv_url": "https://pull.example/live.flv",
            "headers": {"User-Agent": "Recorder", "Referer": "https://live.douyin.com/"},
        }
        values.update(overrides)
        return StreamInfo(**values)

    def build_command(self, recorder, output_dir):
        target = RecordingTarget(
            url="https://live.douyin.com/123",
            display_name=' Anchor / A:*? "test" ',
        )
        with patch(
            "douyinliverecorder.recorders.http_stream.time.strftime",
            return_value="2026-07-14_12-34-56",
        ):
            return recorder.build_command(
                target,
                self.live_stream(),
                output_dir,
                RecorderOptions(),
            )

    def test_build_command_prefers_flv_and_is_structured_immutable(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))

        command = self.build_command(recorder, Path("downloads"))

        self.assertIsInstance(command, HTTPStreamCommand)
        self.assertEqual(command.url, "https://pull.example/live.flv")
        self.assertEqual(
            command.output_path,
            Path("downloads") / "Anchor_A_test_4c70255c_2026-07-14_12-34-56.flv",
        )
        self.assertEqual(
            command.headers,
            (("User-Agent", "Recorder"), ("Referer", "https://live.douyin.com/")),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            command.url = "https://example.com/other.flv"

    def test_direct_http_recorder_name_is_android_compatible(self):
        self.assertIs(DirectHttpRecorder, HTTPStreamRecorder)

    def test_build_command_falls_back_to_non_hls_primary_url(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = self.live_stream(
            flv_url="",
            primary_url="https://pull.example/live.bin",
        )

        command = recorder.build_command(target, stream, Path("downloads"), RecorderOptions())

        self.assertEqual(command.url, "https://pull.example/live.bin")

    def test_build_command_rejects_hls_without_android_ffmpeg(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))
        stream = self.live_stream(flv_url="")

        with self.assertRaisesRegex(ValueError, "HLS"):
            recorder.build_command(
                RecordingTarget(url="https://live.douyin.com/123"),
                stream,
                Path("downloads"),
                RecorderOptions(),
            )

    def test_build_command_carries_proxy_configuration(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))

        command = recorder.build_command(
            RecordingTarget(url="https://live.douyin.com/123"),
            self.live_stream(),
            Path("downloads"),
            RecorderOptions(proxy_addr="127.0.0.1:7890"),
        )

        self.assertEqual(command.proxy_addr, "127.0.0.1:7890")

    def test_build_command_rejects_offline_stream(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))
        stream = self.live_stream(is_live=False)

        with self.assertRaisesRegex(ValueError, "not live"):
            recorder.build_command(
                RecordingTarget(url="https://live.douyin.com/123"),
                stream,
                Path("downloads"),
                RecorderOptions(),
            )

    def test_build_command_rejects_live_stream_without_url(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))
        stream = self.live_stream(flv_url="", primary_url="")

        with self.assertRaisesRegex(ValueError, "recording URL"):
            recorder.build_command(
                RecordingTarget(url="https://live.douyin.com/123"),
                stream,
                Path("downloads"),
                RecorderOptions(),
            )

    def test_build_command_does_not_create_output_directory(self):
        recorder = HTTPStreamRecorder(opener=FakeOpener(FakeResponse()))
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "missing"

            self.build_command(recorder, output_dir)

            self.assertFalse(output_dir.exists())

    def test_start_streams_chunks_with_headers_and_renames_part_file(self):
        response = FakeResponse([b"first", b"-second"])
        opener = FakeOpener(response)
        recorder = HTTPStreamRecorder(opener=opener, chunk_size=4)
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "nested"
            command = self.build_command(recorder, output_dir)

            process = recorder.start(command)
            returncode = process.wait(timeout=1)

            self.assertEqual(returncode, 0)
            self.assertEqual(process.poll(), 0)
            self.assertIsNone(process.error)
            self.assertTrue(process.thread.daemon)
            self.assertEqual(command.output_path.read_bytes(), b"first-second")
            self.assertFalse(command.part_path.exists())
            self.assertTrue(response.closed)
            self.assertEqual(response.read_sizes, [4, 4, 4])
            self.assertEqual(len(opener.requests), 1)
            request = opener.requests[0]
            self.assertEqual(request.full_url, command.url)
            self.assertEqual(request.get_header("User-agent"), "Recorder")
            self.assertEqual(request.get_header("Referer"), "https://live.douyin.com/")

    def test_poll_is_none_while_alive_and_stop_closes_response_cleanly(self):
        class BlockingResponse:
            def __init__(self):
                self.read_started = threading.Event()
                self.closed = threading.Event()

            def read(self, _size):
                self.read_started.set()
                self.closed.wait(timeout=1)
                raise OSError("response closed")

            def close(self):
                self.closed.set()

        response = BlockingResponse()
        recorder = HTTPStreamRecorder(opener=FakeOpener(response))
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))
            process = recorder.start(command)
            self.assertTrue(response.read_started.wait(timeout=1))

            self.assertIsNone(process.poll())
            recorder.stop(process, timeout=1)

            self.assertTrue(response.closed.is_set())
            self.assertEqual(process.poll(), 0)
            self.assertIsNone(process.error)
            self.assertTrue(command.output_path.exists())
            self.assertFalse(command.part_path.exists())

    def test_read_failure_sets_nonzero_status_and_preserves_part_file(self):
        failure = OSError("connection lost")
        response = FakeResponse([b"partial"], error=failure)
        recorder = HTTPStreamRecorder(opener=FakeOpener(response))
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))

            process = recorder.start(command)

            self.assertEqual(process.wait(timeout=1), 1)
            self.assertEqual(process.poll(), 1)
            self.assertIs(process.error, failure)
            self.assertFalse(command.output_path.exists())
            self.assertEqual(command.part_path.read_bytes(), b"partial")
            self.assertTrue(response.closed)

    def test_open_failure_sets_nonzero_status_and_exposes_error(self):
        failure = OSError("cannot connect")

        def failing_opener(_request):
            raise failure

        recorder = HTTPStreamRecorder(opener=failing_opener)
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))

            process = recorder.start(command)

            self.assertEqual(process.wait(timeout=1), 1)
            self.assertIs(process.error, failure)
            self.assertFalse(command.output_path.exists())
            self.assertFalse(command.part_path.exists())

    def test_stop_raises_when_worker_exceeds_bounded_timeout(self):
        class StubbornResponse:
            def __init__(self):
                self.read_started = threading.Event()
                self.release = threading.Event()
                self.closed = False

            def read(self, _size):
                self.read_started.set()
                self.release.wait(timeout=1)
                return b""

            def close(self):
                self.closed = True

        response = StubbornResponse()
        recorder = HTTPStreamRecorder(opener=FakeOpener(response))
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))
            process = recorder.start(command)
            self.assertTrue(response.read_started.wait(timeout=1))

            with self.assertRaisesRegex(TimeoutError, "did not stop"):
                recorder.stop(process, timeout=0.01)

            self.assertTrue(response.closed)
            self.assertIsNone(process.poll())
            response.release.set()
            self.assertEqual(process.wait(timeout=1), 0)

    def test_stop_is_noop_after_completion(self):
        response = FakeResponse()
        recorder = HTTPStreamRecorder(opener=FakeOpener(response))
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))
            process = recorder.start(command)
            self.assertEqual(process.wait(timeout=1), 0)

            recorder.stop(process, timeout=0)

            self.assertEqual(process.poll(), 0)

    def test_force_stop_requests_stop_and_waits_for_worker(self):
        response = FakeResponse([b"data"])
        recorder = HTTPStreamRecorder(opener=FakeOpener(response))
        with TemporaryDirectory() as temp_dir:
            command = self.build_command(recorder, Path(temp_dir))
            process = recorder.start(command)

            recorder.force_stop(process, timeout=1)

            self.assertIsNotNone(process.poll())
            self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
