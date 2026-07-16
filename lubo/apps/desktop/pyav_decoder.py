from __future__ import annotations

import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from lubo.core.models import StreamInfo


logger = logging.getLogger(__name__)
_CANCELLED = object()


@dataclass(frozen=True, slots=True)
class DecodedFrame:
    width: int
    height: int
    rgba: bytes


class PreviewDecodeError(RuntimeError):
    pass


class _OwnedHttpContainer:
    def __init__(self, container: Any, response: Any) -> None:
        self._container = container
        self._response = response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._container, name)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._container.close()


class PyAvDecoder:
    _MAX_WIDTH = 1280
    _MAX_HEIGHT = 720
    _MIN_FRAME_INTERVAL = 1.0 / 15.0

    def __init__(
        self,
        open_container: Callable[..., Any] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open_container = open_container or self._default_open_container
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._container: Any | None = None
        self._active = False
        self._close_epoch = 0

    def frames(
        self,
        stream: StreamInfo,
        stop_event: threading.Event,
    ) -> Iterator[DecodedFrame]:
        with self._lock:
            if self._active:
                raise PreviewDecodeError("Preview decoding is already active.")
            self._active = True
            open_epoch = self._close_epoch

        container: Any | None = None
        try:
            if stop_event.is_set():
                return

            url = self._select_url(stream)
            open_kwargs: dict[str, Any] = {
                "mode": "r",
                "timeout": (10.0, 3.0),
            }
            headers = self._build_http_headers(stream)
            if headers and not self._is_hls_url(stream, url):
                open_kwargs["http_headers"] = headers
            open_failed = False
            try:
                container = self._open_container(url, **open_kwargs)
            except Exception:
                open_failed = True
            if open_failed:
                if self._cancelled(stop_event, open_epoch=open_epoch):
                    return
                raise PreviewDecodeError("Unable to open preview stream.")

            with self._lock:
                attach = self._close_epoch == open_epoch
                if attach:
                    self._container = container
            if not attach:
                self._close_during_cleanup(container)
                container = None
                return
            if stop_event.is_set():
                return

            video_stream = self._first_video_stream(container, stop_event)
            if video_stream is _CANCELLED:
                return
            last_delivery: float | None = None
            decode_failed = False
            try:
                decoded_frames = container.decode(video_stream)
                for source_frame in decoded_frames:
                    if stop_event.is_set() or not self._owns(container):
                        break

                    now = self._monotonic()
                    if (
                        last_delivery is not None
                        and now - last_delivery < self._MIN_FRAME_INTERVAL
                    ):
                        continue

                    frame = self._convert_frame(source_frame)
                    if stop_event.is_set() or not self._owns(container):
                        break
                    last_delivery = now
                    yield frame
            except PreviewDecodeError:
                raise
            except Exception:
                if not stop_event.is_set() and self._owns(container):
                    decode_failed = True
            if decode_failed:
                raise PreviewDecodeError("Unable to decode preview stream.")
        finally:
            if container is not None:
                self._detach_and_close(container)
            with self._lock:
                self._active = False

    def close(self) -> None:
        with self._lock:
            self._close_epoch += 1
            container = self._container
            self._container = None
        if container is not None:
            close_failed = False
            try:
                container.close()
            except Exception:
                close_failed = True
            if close_failed:
                raise PreviewDecodeError("Unable to close preview stream.")

    @staticmethod
    def _default_open_container(url: str, **kwargs: Any) -> Any:
        import av

        headers = kwargs.pop("http_headers", None)
        if not headers:
            return av.open(url, **kwargs)

        timeout = kwargs.pop("timeout", (10.0, 3.0))
        open_timeout = float(timeout[0] if isinstance(timeout, tuple) else timeout)
        request = urllib.request.Request(url, headers=headers)
        response = urllib.request.urlopen(request, timeout=open_timeout)
        try:
            container = av.open(response, **kwargs)
        except Exception:
            response.close()
            raise
        return _OwnedHttpContainer(container, response)

    @staticmethod
    def _select_url(stream: StreamInfo) -> str:
        for candidate in (stream.primary_url, stream.flv_url, stream.hls_url):
            url = candidate.strip()
            if url:
                return url
        raise PreviewDecodeError("Preview stream has no usable URL.")

    @staticmethod
    def _build_http_headers(stream: StreamInfo) -> dict[str, str]:
        safe_values: dict[str, str] = {}
        for name, value in stream.headers.items():
            normalized_name = name.strip().casefold()
            if normalized_name not in {"user-agent", "referer", "origin"}:
                continue
            if "\r" in value or "\n" in value:
                continue
            safe_values[normalized_name] = value

        headers: dict[str, str] = {}
        for normalized_name, display_name in (
            ("user-agent", "User-Agent"),
            ("referer", "Referer"),
            ("origin", "Origin"),
        ):
            if normalized_name in safe_values:
                headers[display_name] = safe_values[normalized_name]
        return headers

    @staticmethod
    def _is_hls_url(stream: StreamInfo, url: str) -> bool:
        if urlsplit(url).path.casefold().endswith(".m3u8"):
            return True
        hls_url = stream.hls_url.strip()
        flv_url = stream.flv_url.strip()
        return bool(hls_url and hls_url == url and flv_url != url)

    def _first_video_stream(
        self,
        container: Any,
        stop_event: threading.Event,
    ) -> Any:
        lookup_failed = False
        try:
            video_streams = container.streams.video
        except Exception:
            lookup_failed = True
            video_streams = ()
        if lookup_failed:
            if self._cancelled(stop_event, container=container):
                return _CANCELLED
            raise PreviewDecodeError("Unable to decode preview stream.")
        if self._cancelled(stop_event, container=container):
            return _CANCELLED
        if not video_streams:
            raise PreviewDecodeError("Preview stream has no video track.")
        return video_streams[0]

    @classmethod
    def _convert_frame(cls, source_frame: Any) -> DecodedFrame:
        width = int(source_frame.width)
        height = int(source_frame.height)
        if width <= 0 or height <= 0:
            raise ValueError("invalid video frame dimensions")

        scale = min(1.0, cls._MAX_WIDTH / width, cls._MAX_HEIGHT / height)
        output_width = max(1, int(width * scale))
        output_height = max(1, int(height * scale))
        reformatted = source_frame.reformat(
            width=output_width,
            height=output_height,
            format="rgba",
        )

        plane = reformatted.planes[0]
        rgba = cls._compact_plane(plane, output_width, output_height)
        return DecodedFrame(
            width=output_width,
            height=output_height,
            rgba=rgba,
        )

    @staticmethod
    def _compact_plane(plane: Any, width: int, height: int) -> bytes:
        row_size = width * 4
        line_size = int(plane.line_size)
        data = bytes(plane)
        if line_size < row_size or len(data) < line_size * height:
            raise ValueError("invalid RGBA plane layout")
        if line_size == row_size:
            return data[: row_size * height]
        return b"".join(
            data[row * line_size : row * line_size + row_size]
            for row in range(height)
        )

    def _owns(self, container: Any) -> bool:
        with self._lock:
            return self._container is container

    def _cancelled(
        self,
        stop_event: threading.Event,
        *,
        open_epoch: int | None = None,
        container: Any | None = None,
    ) -> bool:
        if stop_event.is_set():
            return True
        with self._lock:
            if open_epoch is not None and self._close_epoch != open_epoch:
                return True
            return container is not None and self._container is not container

    def _detach_and_close(self, container: Any) -> None:
        with self._lock:
            if self._container is not container:
                return
            self._container = None
        self._close_during_cleanup(container)

    @staticmethod
    def _close_during_cleanup(container: Any) -> None:
        try:
            container.close()
        except Exception:
            logger.warning("Unable to close preview stream during cleanup.")
