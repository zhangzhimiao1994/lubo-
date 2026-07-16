import unittest
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lubo.core.models import OutputFormat, RecordingTarget, StreamInfo
from lubo.recorders.ffmpeg import FFmpegRecorder, RecorderOptions


class FFmpegRecorderTests(unittest.TestCase):
    def live_stream(self, headers=None):
        return StreamInfo(
            platform_key="douyin",
            platform_name="Douyin",
            anchor_name="Anchor A",
            is_live=True,
            primary_url="https://pull.example/live.m3u8",
            headers=headers or {},
        )

    def test_builds_segmented_ts_command(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123", display_name="主播A")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")

        command = recorder.build_command(target, stream, Path("downloads"), RecorderOptions(output_format=OutputFormat.TS, split_enabled=True, split_seconds=1800))

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-segment_time", command)
        self.assertIn("1800", command)
        self.assertIn("https://pull.example/live.m3u8", command)
        self.assertTrue(command[-1].endswith("_%03d.ts"))

    def test_video_recording_maps_only_video_and_audio_streams(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")

        command = recorder.build_command(
            RecordingTarget(url="https://www.huya.com/123"),
            self.live_stream(),
            Path("downloads"),
            RecorderOptions(output_format=OutputFormat.TS, split_enabled=False),
        )

        mapped_streams = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-map"
        ]
        self.assertEqual(mapped_streams, ["0:v?", "0:a?"])
        self.assertNotIn("0", mapped_streams)

    def test_builds_mp4_command_without_segments(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")

        command = recorder.build_command(target, stream, Path("downloads"), RecorderOptions(output_format=OutputFormat.MP4, split_enabled=False))

        self.assertIn("-f", command)
        self.assertIn("mp4", command)
        self.assertTrue(command[-1].endswith(".mp4"))

    def test_rejects_not_live_stream(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin")

        with self.assertRaises(ValueError):
            recorder.build_command(target, stream, Path("downloads"), RecorderOptions())

    def test_build_command_does_not_create_output_directory(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "missing"

            recorder.build_command(target, stream, output_dir, RecorderOptions())

            self.assertFalse(output_dir.exists())

    def test_build_command_omits_headers_argument_when_headers_empty(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = self.live_stream()

        command = recorder.build_command(target, stream, Path("downloads"), RecorderOptions())

        self.assertNotIn("-headers", command)

    def test_build_command_includes_formatted_headers_when_present(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = self.live_stream(headers={"User-Agent": "Recorder", "Referer": "https://live.douyin.com/"})

        command = recorder.build_command(target, stream, Path("downloads"), RecorderOptions())

        headers_index = command.index("-headers")
        self.assertEqual(command[headers_index + 1], "User-Agent: Recorder\r\nReferer: https://live.douyin.com/\r\n")

    def test_build_command_supports_audio_only_formats(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = self.live_stream()

        for output_format in (OutputFormat.MP3, OutputFormat.M4A):
            with self.subTest(output_format=output_format):
                command = recorder.build_command(
                    target,
                    stream,
                    Path("downloads"),
                    RecorderOptions(output_format=output_format, split_enabled=False),
                )

                self.assertIn("-vn", command)
                self.assertTrue(command[-1].endswith(f".{output_format.value}"))

    def test_convert_to_mp4_records_video_with_mp4_muxer_and_extension(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")

        command = recorder.build_command(
            target,
            self.live_stream(),
            Path("downloads"),
            RecorderOptions(
                output_format=OutputFormat.TS,
                split_enabled=False,
                convert_to_mp4=True,
            ),
        )

        self.assertEqual(command[command.index("-f") + 1], "mp4")
        self.assertTrue(command[-1].endswith(".mp4"))
        self.assertIn("-movflags", command)
        self.assertIn("+frag_keyframe+empty_moov+default_base_moof", command)

    def test_build_command_passes_http_proxy_to_ffmpeg(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")

        command = recorder.build_command(
            RecordingTarget(url="https://live.douyin.com/123"),
            self.live_stream(),
            Path("downloads"),
            RecorderOptions(proxy_addr="127.0.0.1:7890"),
        )

        proxy_index = command.index("-http_proxy")
        self.assertEqual(command[proxy_index + 1], "http://127.0.0.1:7890")
        self.assertLess(proxy_index, command.index("-i"))

    def test_same_name_targets_get_distinct_output_paths(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        first = RecordingTarget(
            url="https://live.douyin.com/111",
            display_name="same-anchor",
        )
        second = RecordingTarget(
            url="https://live.douyin.com/222",
            display_name="same-anchor",
        )

        first_command = recorder.build_command(
            first, self.live_stream(), Path("downloads"), RecorderOptions()
        )
        second_command = recorder.build_command(
            second, self.live_stream(), Path("downloads"), RecorderOptions()
        )

        self.assertNotEqual(first_command[-1], second_command[-1])

    def test_build_command_rejects_non_positive_split_seconds_when_split_enabled(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = self.live_stream()

        with self.assertRaises(ValueError):
            recorder.build_command(target, stream, Path("downloads"), RecorderOptions(split_enabled=True, split_seconds=0))

    def test_start_discards_output_and_opens_stdin_for_graceful_stop(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        command = ["ffmpeg", "-version"]

        with patch("subprocess.Popen") as popen:
            recorder.start(command)

        popen.assert_called_once_with(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_stop_sends_q_and_waits_for_ffmpeg(self):
        class FakeStdin:
            def __init__(self):
                self.calls = []

            def write(self, value):
                self.calls.append(("write", value))

            def flush(self):
                self.calls.append(("flush",))

        class FakeProcess:
            def __init__(self):
                self.stdin = FakeStdin()
                self.calls = []

            def poll(self):
                self.calls.append(("poll",))
                return None

            def wait(self, timeout=None):
                self.calls.append(("wait", timeout))
                return 0

        process = FakeProcess()
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")

        recorder.stop(process, timeout=5)

        self.assertEqual(process.stdin.calls, [("write", b"q\n"), ("flush",)])
        self.assertEqual(process.calls, [("poll",), ("wait", 5)])

    def test_stop_timeout_after_q_terminates_then_kills_and_waits_again(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = unittest.mock.MagicMock()
                self.calls = []

            def poll(self):
                self.calls.append(("poll",))
                return None

            def terminate(self):
                self.calls.append(("terminate",))

            def wait(self, timeout=None):
                self.calls.append(("wait", timeout))
                if timeout == 5:
                    raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
                return 0

            def kill(self):
                self.calls.append(("kill",))

        process = FakeProcess()
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")

        recorder.stop(process, timeout=5)

        self.assertEqual(
            process.calls,
            [("poll",), ("wait", 5), ("terminate",), ("wait", 5), ("kill",), ("wait", None)],
        )

    def test_stop_falls_back_to_terminate_when_stdin_is_broken(self):
        class BrokenStdin:
            def write(self, _value):
                raise BrokenPipeError

        process = unittest.mock.MagicMock()
        process.poll.return_value = None
        process.stdin = BrokenStdin()

        FFmpegRecorder().stop(process, timeout=3)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)

    def test_force_stop_kills_running_process_and_waits(self):
        process = unittest.mock.MagicMock()
        process.poll.return_value = None

        FFmpegRecorder().force_stop(process)

        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
