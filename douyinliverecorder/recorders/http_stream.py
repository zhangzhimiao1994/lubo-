from __future__ import annotations

import hashlib
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from douyinliverecorder.core.models import RecordingTarget, StreamInfo
from douyinliverecorder.recorders.ffmpeg import RecorderOptions, safe_name


@dataclass(frozen=True, slots=True)
class HTTPStreamCommand:
    url: str
    output_path: Path
    headers: tuple[tuple[str, str], ...] = ()
    proxy_addr: str = ""

    @property
    def part_path(self) -> Path:
        return self.output_path.with_name(f"{self.output_path.name}.part")


class HTTPStreamProcess:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._response: Any | None = None
        self._thread: threading.Thread | None = None
        self._returncode: int | None = None
        self._error: Exception | None = None

    @property
    def thread(self) -> threading.Thread:
        if self._thread is None:
            raise RuntimeError("recorder thread has not been started")
        return self._thread

    @property
    def error(self) -> Exception | None:
        with self._state_lock:
            return self._error

    def poll(self) -> int | None:
        thread = self._thread
        if thread is None or thread.is_alive():
            return None
        with self._state_lock:
            return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        thread = self.thread
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("HTTP stream recorder is still running")
        with self._state_lock:
            if self._returncode is None:
                raise RuntimeError("HTTP stream recorder exited without a status")
            return self._returncode

    def _attach_thread(self, thread: threading.Thread) -> None:
        self._thread = thread

    def _activate_response(self, response: Any) -> None:
        close_immediately = False
        with self._state_lock:
            if self._stop_event.is_set():
                close_immediately = True
            else:
                self._response = response
        if close_immediately:
            self._close_response(response)

    def _deactivate_response(self, response: Any) -> None:
        with self._state_lock:
            if self._response is response:
                self._response = None

    def _request_stop(self) -> None:
        self._stop_event.set()
        with self._state_lock:
            response = self._response
        if response is not None:
            self._close_response(response)

    def _finish(self, returncode: int, error: Exception | None = None) -> None:
        with self._state_lock:
            self._returncode = returncode
            self._error = error

    @staticmethod
    def _close_response(response: Any) -> None:
        try:
            response.close()
        except Exception:
            pass


class HTTPStreamRecorder:
    def __init__(
        self,
        opener: Callable[[urllib.request.Request], Any] | None = None,
        chunk_size: int = 64 * 1024,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")
        self._opener = opener
        self._chunk_size = chunk_size

    def build_command(
        self,
        target: RecordingTarget,
        stream: StreamInfo,
        output_dir: Path,
        options: RecorderOptions,
    ) -> HTTPStreamCommand:
        if not stream.is_live:
            raise ValueError("stream is not live")
        url = stream.flv_url or stream.primary_url
        if not url:
            raise ValueError("stream has no recording URL")
        if ".m3u8" in url.lower().split("?", 1)[0]:
            raise ValueError("HLS streams require an Android FFmpeg build")

        anchor = target.display_name or stream.anchor_name or "live"
        target_key = hashlib.sha256(target.url.encode("utf-8")).hexdigest()[:8]
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        output_path = Path(output_dir) / (
            f"{safe_name(anchor)}_{target_key}_{timestamp}.flv"
        )
        return HTTPStreamCommand(
            url=url,
            output_path=output_path,
            headers=tuple(stream.headers.items()),
            proxy_addr=options.proxy_addr,
        )

    def start(self, command: HTTPStreamCommand) -> HTTPStreamProcess:
        command.output_path.parent.mkdir(parents=True, exist_ok=True)
        process = HTTPStreamProcess()
        thread = threading.Thread(
            target=self._record,
            args=(command, process),
            name=f"http-stream-{command.output_path.stem}",
            daemon=True,
        )
        process._attach_thread(thread)
        thread.start()
        return process

    def stop(self, process: HTTPStreamProcess, timeout: float = 10) -> None:
        if process.poll() is not None:
            return
        process._request_stop()
        process.thread.join(timeout)
        if process.thread.is_alive():
            raise TimeoutError("HTTP stream recorder did not stop within the timeout")

    def force_stop(self, process: HTTPStreamProcess, timeout: float = 35) -> None:
        if process.poll() is not None:
            return
        process._request_stop()
        process.thread.join(timeout)
        if process.thread.is_alive():
            raise TimeoutError(
                "HTTP stream recorder did not exit after forced response closure"
            )

    def _record(self, command: HTTPStreamCommand, process: HTTPStreamProcess) -> None:
        response: Any | None = None
        try:
            request = urllib.request.Request(command.url, headers=dict(command.headers))
            response = self._open(request, command.proxy_addr)
            process._activate_response(response)

            with command.part_path.open("wb") as output_file:
                while not process._stop_event.is_set():
                    try:
                        chunk = response.read(self._chunk_size)
                    except Exception:
                        if process._stop_event.is_set():
                            break
                        raise
                    if not chunk:
                        break
                    output_file.write(chunk)

            command.part_path.replace(command.output_path)
        except Exception as exc:
            process._finish(1, exc)
        else:
            process._finish(0)
        finally:
            if response is not None:
                process._deactivate_response(response)
                process._close_response(response)

    def _open(self, request: urllib.request.Request, proxy_addr: str) -> Any:
        if self._opener is not None:
            return self._opener(request)
        if not proxy_addr:
            return urllib.request.urlopen(request, timeout=30)
        proxy_url = proxy_addr if "://" in proxy_addr else f"http://{proxy_addr}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        return opener.open(request, timeout=30)


DirectHttpRecorder = HTTPStreamRecorder
