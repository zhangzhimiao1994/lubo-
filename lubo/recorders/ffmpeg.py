from __future__ import annotations

import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from lubo.core.models import OutputFormat, RecordingTarget, StreamInfo


@dataclass(frozen=True, slots=True)
class RecorderOptions:
    output_format: OutputFormat = OutputFormat.TS
    split_enabled: bool = True
    split_seconds: int = 1800
    convert_to_mp4: bool = False
    proxy_addr: str = ""


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\/\\:*?"<>|&#.\s]+', "_", value.strip())
    return cleaned.strip("_") or "live"


class FFmpegRecorder:
    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    def build_command(self, target: RecordingTarget, stream: StreamInfo, output_dir: Path, options: RecorderOptions) -> list[str]:
        if not stream.is_live or not stream.primary_url:
            raise ValueError("stream is not live or has no recording URL")
        if options.split_enabled and options.split_seconds <= 0:
            raise ValueError("split_seconds must be greater than 0 when splitting is enabled")
        audio_only = options.output_format in (OutputFormat.MP3, OutputFormat.M4A)
        output_format = (
            OutputFormat.MP4
            if options.convert_to_mp4 and not audio_only
            else options.output_format
        )
        stem = self._stem(target, stream)
        input_args = [self.ffmpeg_path, "-y"]
        headers = self._headers(stream)
        if headers:
            input_args.extend(["-headers", headers])
        if options.proxy_addr:
            input_args.extend(["-http_proxy", self._proxy_url(options.proxy_addr)])
        input_args.extend(["-i", stream.primary_url])
        if audio_only:
            codec_args = [
                "-vn",
                "-c:a",
                "libmp3lame" if output_format == OutputFormat.MP3 else "aac",
            ]
        else:
            codec_args = [
                "-c:v",
                "copy",
                "-c:a",
                "aac" if output_format == OutputFormat.MP4 else "copy",
                "-map",
                "0",
            ]
        muxer = "mpegts" if output_format == OutputFormat.TS else (
            "ipod" if output_format == OutputFormat.M4A else output_format.value
        )
        if options.split_enabled:
            output_path = output_dir / f"{stem}_%03d.{output_format.value}"
            segment_options: list[str] = []
            if output_format == OutputFormat.MP4:
                segment_options = [
                    "-segment_format_options",
                    "movflags=+frag_keyframe+empty_moov+default_base_moof",
                ]
            return [
                *input_args,
                *codec_args,
                "-f",
                "segment",
                "-segment_time",
                str(options.split_seconds),
                "-segment_format",
                muxer,
                *segment_options,
                "-reset_timestamps",
                "1",
                str(output_path),
            ]
        output_path = output_dir / f"{stem}.{output_format.value}"
        muxer_options = (
            ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"]
            if output_format == OutputFormat.MP4
            else []
        )
        return [
            *input_args,
            *codec_args,
            *muxer_options,
            "-f",
            muxer,
            str(output_path),
        ]

    def start(self, command: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self, process: subprocess.Popen, timeout: int = 10) -> None:
        if process.poll() is not None:
            return

        graceful = False
        if process.stdin is not None:
            try:
                process.stdin.write(b"q\n")
                process.stdin.flush()
                graceful = True
            except (BrokenPipeError, OSError, ValueError):
                pass

        if graceful:
            try:
                process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                pass

        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def force_stop(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.kill()
        process.wait()

    def _stem(self, target: RecordingTarget, stream: StreamInfo) -> str:
        anchor = target.display_name or stream.anchor_name or "live"
        target_key = hashlib.sha256(target.url.encode("utf-8")).hexdigest()[:8]
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        return f"{safe_name(anchor)}_{target_key}_{timestamp}"

    def _proxy_url(self, proxy_addr: str) -> str:
        value = proxy_addr.strip()
        if "://" not in value:
            return f"http://{value}"
        return value

    def _headers(self, stream: StreamInfo) -> str:
        return "".join(f"{key}: {value}\r\n" for key, value in stream.headers.items())
