from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from lubo.apps.desktop.controller import DesktopController
from lubo.apps.desktop.runtime import resolve_ffmpeg
from lubo.core.config import ConfigService
from lubo.core.events import EventBus, RecorderEvent
from lubo.core.scheduler import RecordingScheduler, SchedulerConfig
from lubo.platforms.factory import build_default_registry
from lubo.recorders.ffmpeg import FFmpegRecorder


logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 60
_QUEUE_SENTINEL = object()


class DaemonTaskQueue:
    def __init__(self, thread_name: str = "desktop-worker") -> None:
        self._queue: Queue[object] = Queue()
        self._lock = Lock()
        self._closed = False
        self._thread = Thread(
            target=self._run,
            name=thread_name,
            daemon=True,
        )
        self._thread.start()

    def submit(self, fn: Callable[[], Any]) -> Future[Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot schedule new tasks after shutdown")
            future: Future[Any] = Future()
            self._queue.put((future, fn))
            return future

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        with self._lock:
            first_shutdown = not self._closed
            self._closed = True
        if first_shutdown:
            if cancel_futures:
                self._cancel_queued_tasks()
            self._queue.put(_QUEUE_SENTINEL)
        if wait:
            self._thread.join()

    def _cancel_queued_tasks(self) -> None:
        while True:
            try:
                future, _fn = self._queue.get_nowait()
            except Empty:
                return
            try:
                future.cancel()
            finally:
                self._queue.task_done()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _QUEUE_SENTINEL:
                    return
                future, fn = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn()
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()


def _resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root)
    return Path(__file__).resolve().parents[3]


def _prepare_user_config(data_dir: Path) -> tuple[Path, Path]:
    config_dir = data_dir / "config"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"无法创建应用配置目录：{config_dir}") from exc

    resource_config_dir = _resource_root() / "config"
    destinations = (
        (resource_config_dir / "config.ini", config_dir / "config.ini"),
        (resource_config_dir / "URL_config.ini", config_dir / "URL_config.ini"),
    )
    for source, destination in destinations:
        if destination.exists() or not source.is_file():
            continue
        try:
            shutil.copy2(source, destination)
        except OSError:
            logger.warning(
                "Could not initialize %s from %s; using service defaults",
                destination,
                source,
                exc_info=True,
            )
    return destinations[0][1], destinations[1][1]


def _prepare_output_dir(data_dir: Path, configured_path: str) -> Path:
    output_dir = Path(configured_path).expanduser() if configured_path else Path("downloads")
    if not output_dir.is_absolute():
        output_dir = data_dir / output_dir
    output_dir = output_dir.resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=output_dir):
            pass
    except OSError as exc:
        raise RuntimeError(f"无法创建或写入录制输出目录：{output_dir}") from exc
    if not output_dir.is_dir():
        raise RuntimeError(f"录制输出路径不是目录：{output_dir}")
    return output_dir


def _fc_list_cjk_fonts() -> list[Path]:
    run_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "--format=%{file}\n"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
            **run_kwargs,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    fonts: list[Path] = []
    seen: set[Path] = set()
    for line in result.stdout.splitlines():
        font_path = Path(line.strip())
        if not line.strip() or font_path in seen or not font_path.is_file():
            continue
        seen.add(font_path)
        fonts.append(font_path)
    return fonts


def _register_cjk_font() -> str | None:
    resource_root = _resource_root()
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [
        resource_root / "fonts" / "NotoSansCJK-Regular.ttc",
        resource_root / "fonts" / "NotoSansCJK-Regular.otf",
        resource_root / "assets" / "fonts" / "NotoSansCJK-Regular.ttc",
        resource_root / "assets" / "fonts" / "NotoSansCJK-Regular.otf",
    ]
    if os.name == "nt":
        candidates.extend(
            (
                windows_fonts / "msyh.ttc",
                windows_fonts / "msyh.ttf",
                windows_fonts / "simhei.ttf",
            )
        )
    if sys.platform.startswith("linux"):
        candidates.extend(_fc_list_cjk_fonts())
    candidates.extend(
        (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        )
    )
    checked: set[Path] = set()
    for font_path in candidates:
        if font_path in checked or not font_path.is_file():
            continue
        checked.add(font_path)
        try:
            LabelBase.register(name="DesktopCJK", fn_regular=str(font_path))
        except Exception:
            logger.warning("Could not register CJK font %s", font_path, exc_info=True)
            continue
        return "DesktopCJK"
    return None


EVENT_NAMES_ZH = {
    "resolve_started": "开始检查",
    "live_detected": "发现直播",
    "offline_detected": "当前未开播",
    "recording_started": "开始录制",
    "recording_stopped": "停止录制",
    "recording_failed": "录制失败",
    "error": "错误",
}

EVENT_NAMES_EN = {
    "resolve_started": "Checking",
    "live_detected": "Live detected",
    "offline_detected": "Offline",
    "recording_started": "Recording started",
    "recording_stopped": "Recording stopped",
    "recording_failed": "Recording failed",
    "error": "Error",
}


class DesktopRoot(BoxLayout):
    def __init__(
        self,
        controller: DesktopController,
        event_bus: EventBus,
        executor: DaemonTaskQueue,
        font_name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(orientation="vertical", spacing=8, padding=10, **kwargs)
        self.controller = controller
        self.event_bus = event_bus
        self.executor = executor
        self.font_name = font_name
        self._use_cjk = font_name is not None
        self._watch_event = None
        self._check_future: Future[None] | None = None
        self._stop_future: Future[None] | None = None
        self._stopping = False
        self._closing = False

        self.url_input = TextInput(
            hint_text=self._text("直播间 URL", "Live room URL"),
            font_name=font_name or "Roboto",
            multiline=False,
            size_hint_y=None,
            height=42,
        )
        self.name_input = TextInput(
            hint_text=self._text("备注（可选）", "Note (optional)"),
            font_name=font_name or "Roboto",
            multiline=False,
            size_hint_y=None,
            height=42,
        )
        self.add_widget(self.url_input)
        self.add_widget(self.name_input)

        controls = BoxLayout(size_hint_y=None, height=44, spacing=8)
        add_button = Button(
            text=self._text("添加", "Add"),
            font_name=font_name or "Roboto",
        )
        add_button.bind(on_press=self._add_target)
        self.watch_button = Button(
            text=self._text("开始值守", "Start monitoring"),
            font_name=font_name or "Roboto",
        )
        self.watch_button.bind(on_press=self._toggle_watch)
        self.stop_button = Button(
            text=self._text("停止全部", "Stop all"),
            font_name=font_name or "Roboto",
        )
        self.stop_button.bind(on_press=self._stop_all)
        controls.add_widget(add_button)
        controls.add_widget(self.watch_button)
        controls.add_widget(self.stop_button)
        self.add_widget(controls)

        self.status = Label(
            text=self._text("值守已暂停", "Monitoring paused"),
            font_name=font_name or "Roboto",
            size_hint_y=None,
            height=32,
            halign="left",
            valign="middle",
        )
        self.status.bind(size=self._fit_status)
        self.add_widget(self.status)

        self.add_widget(
            Label(
                text=self._text("录制目标", "Recording targets"),
                font_name=font_name or "Roboto",
                size_hint_y=None,
                height=28,
            )
        )
        self.target_list = BoxLayout(
            orientation="vertical",
            spacing=4,
            size_hint_y=None,
        )
        self.target_list.bind(minimum_height=self.target_list.setter("height"))
        target_scroll = ScrollView(size_hint_y=0.45)
        target_scroll.add_widget(self.target_list)
        self.add_widget(target_scroll)

        self.add_widget(
            Label(
                text=self._text("事件日志", "Event log"),
                font_name=font_name or "Roboto",
                size_hint_y=None,
                height=28,
            )
        )
        self.log = Label(
            text="",
            font_name=font_name or "Roboto",
            size_hint_y=None,
            halign="left",
            valign="top",
        )
        self.log.bind(texture_size=self._fit_log, width=self._fit_log)
        log_scroll = ScrollView(size_hint_y=0.55)
        log_scroll.add_widget(self.log)
        self.add_widget(log_scroll)

        event_bus.subscribe(self._on_event)
        self._render_targets()

    def _text(self, chinese: str, english: str) -> str:
        return chinese if self._use_cjk else english

    def _fit_status(self, widget: Label, size: tuple[float, float]) -> None:
        widget.text_size = (size[0], None)

    def _fit_log(self, widget: Label, *_args) -> None:
        widget.text_size = (widget.width, None)
        widget.height = max(widget.texture_size[1], 24)

    def _add_target(self, _button) -> None:
        url = self.url_input.text.strip()
        if not url:
            self.status.text = self._text(
                "请输入直播间 URL",
                "Enter a live room URL",
            )
            return
        try:
            self.controller.add_target(
                url,
                name=self.name_input.text.strip(),
            )
        except Exception as exc:
            prefix = self._text("添加失败：", "Add failed: ")
            self.status.text = f"{prefix}{exc}"
            self._append_log(self.status.text)
            return
        self.url_input.text = ""
        self.name_input.text = ""
        self.status.text = self._text("目标已添加", "Target added")
        self._render_targets()

    def _toggle_watch(self, _button) -> None:
        if self._stopping:
            self.status.text = self._text(
                "正在停止全部录制，请稍候",
                "Stopping all recordings, please wait",
            )
            return
        if self._watch_event is not None:
            self.stop_watch()
            self.status.text = self._text("值守已暂停", "Monitoring paused")
            return

        interval = max(1, int(self.controller.config.loop_seconds))
        self._watch_event = Clock.schedule_interval(self._scheduled_check, interval)
        self.watch_button.text = self._text("暂停值守", "Pause monitoring")
        self.status.text = self._monitoring_status(interval)
        self._submit_check()

    def _scheduled_check(self, _dt) -> None:
        self._submit_check()

    def _submit_check(self) -> None:
        if self._closing or self._stopping:
            return
        if self._check_future is not None and not self._check_future.done():
            return
        self.status.text = self._text(
            "正在检查直播状态...",
            "Checking live status...",
        )
        future = self.executor.submit(self._run_check)
        self._check_future = future
        future.add_done_callback(self._schedule_check_result)

    def _schedule_check_result(self, future: Future[None]) -> None:
        if not self._closing:
            Clock.schedule_once(lambda _dt: self._finish_check(future), 0)

    def _run_check(self) -> None:
        asyncio.run(
            asyncio.wait_for(
                self.controller.check_once(),
                timeout=CHECK_TIMEOUT_SECONDS,
            )
        )

    def _finish_check(self, future: Future[None]) -> None:
        if self._closing:
            return
        try:
            future.result()
        except Exception as exc:
            prefix = self._text("检查失败：", "Check failed: ")
            detail = str(exc) or self._text(
                f"检查超时（{CHECK_TIMEOUT_SECONDS} 秒）",
                f"Timed out after {CHECK_TIMEOUT_SECONDS} seconds",
            )
            self.status.text = f"{prefix}{detail}"
            self._append_log(self.status.text)
        else:
            if self._stopping:
                self.status.text = self._text(
                    "正在停止全部录制...",
                    "Stopping all recordings...",
                )
            elif self._watch_event is not None:
                interval = max(1, int(self.controller.config.loop_seconds))
                self.status.text = self._monitoring_status(interval)
            else:
                self.status.text = self._text("检查完成", "Check complete")
        self._render_targets()

    def _stop_all(self, _button) -> None:
        if self._closing or self._stopping:
            return
        self.stop_watch()
        self._stopping = True
        self.watch_button.disabled = True
        self.stop_button.disabled = True
        self.status.text = self._text(
            "正在停止全部录制...",
            "Stopping all recordings...",
        )
        try:
            future = self.executor.submit(self.controller.stop_all)
        except Exception as exc:
            self._stopping = False
            self.watch_button.disabled = False
            self.stop_button.disabled = False
            prefix = self._text("停止失败：", "Stop failed: ")
            self.status.text = f"{prefix}{exc}"
            self._append_log(self.status.text)
            return
        self._stop_future = future
        future.add_done_callback(self._schedule_stop_result)

    def _schedule_stop_result(self, future: Future[None]) -> None:
        if not self._closing:
            Clock.schedule_once(lambda _dt: self._finish_stop(future), 0)

    def _finish_stop(self, future: Future[None]) -> None:
        if self._closing:
            return
        self._stopping = False
        self.watch_button.disabled = False
        self.stop_button.disabled = False
        try:
            future.result()
        except Exception as exc:
            prefix = self._text("停止失败：", "Stop failed: ")
            self.status.text = f"{prefix}{exc}"
            self._append_log(self.status.text)
        else:
            self.status.text = self._text(
                "已停止全部录制",
                "All recordings stopped",
            )

    def _on_event(self, event: RecorderEvent) -> None:
        Clock.schedule_once(lambda _dt, item=event: self._show_event(item), 0)

    def _show_event(self, event: RecorderEvent) -> None:
        if self._closing:
            return
        event_names = EVENT_NAMES_ZH if self._use_cjk else EVENT_NAMES_EN
        event_name = event_names.get(event.type.value, event.type.value)
        separator = "：" if self._use_cjk else ": "
        detail = f"{separator}{event.message}" if event.message else ""
        timestamp = datetime.fromtimestamp(event.created_at).strftime("%H:%M:%S")
        self._append_log(f"[{timestamp}] {event_name} {event.target_id}{detail}")

    def _append_log(self, text: str) -> None:
        lines = [*self.log.text.splitlines(), text]
        self.log.text = "\n".join(lines[-500:])

    def _render_targets(self) -> None:
        self.target_list.clear_widgets()
        if not self.controller.targets:
            self.target_list.add_widget(
                Label(
                    text=self._text("暂无录制目标", "No recording targets"),
                    font_name=self.font_name or "Roboto",
                    size_hint_y=None,
                    height=36,
                )
            )
            return
        for target in self.controller.targets:
            state = (
                self._text("启用", "Enabled")
                if target.enabled
                else self._text("暂停", "Paused")
            )
            name = f"{target.display_name}  " if target.display_name else ""
            label = Label(
                text=f"[{state}] {name}{target.url}",
                font_name=self.font_name or "Roboto",
                size_hint_y=None,
                height=36,
                halign="left",
                valign="middle",
                shorten=True,
                shorten_from="right",
                max_lines=1,
            )
            label.bind(size=self._fit_target_label)
            self.target_list.add_widget(label)

    def _fit_target_label(self, widget: Label, size: tuple[float, float]) -> None:
        widget.text_size = size

    def stop_watch(self) -> None:
        if self._watch_event is not None:
            self._watch_event.cancel()
            self._watch_event = None
        self.watch_button.text = self._text("开始值守", "Start monitoring")

    def _monitoring_status(self, interval: int) -> str:
        return self._text(
            f"值守中，每 {interval} 秒检查一次",
            f"Monitoring; checking every {interval} seconds",
        )

    def close(self) -> None:
        self._closing = True
        self.stop_watch()
        self.event_bus.unsubscribe(self._on_event)


class LuboDesktopApp(App):
    def build(self) -> DesktopRoot:
        data_dir = Path(self.user_data_dir).expanduser().resolve()
        config_path, url_path = _prepare_user_config(data_dir)
        config_service = ConfigService(config_path)
        config = config_service.load()
        output_dir = _prepare_output_dir(data_dir, config.save_path)
        event_bus = EventBus()
        registry = build_default_registry()
        recorder = FFmpegRecorder(ffmpeg_path=resolve_ffmpeg())
        self.scheduler = RecordingScheduler(
            registry=registry,
            recorder=recorder,
            event_bus=event_bus,
            config=SchedulerConfig(
                output_dir=output_dir,
                quality=config.quality,
                proxy_addr=config.proxy_addr if config.use_proxy else "",
                cookies=dict(config.cookies),
                output_format=config.output_format,
                split_enabled=config.split_enabled,
                split_seconds=config.split_seconds,
                max_concurrency=config.max_concurrency,
                convert_to_mp4=config.convert_to_mp4,
            ),
        )
        self.event_bus = event_bus
        self.controller = DesktopController(
            config_service,
            url_path,
            self.scheduler,
        )
        self.executor = DaemonTaskQueue(thread_name="recorder-check")
        self.desktop_root = DesktopRoot(
            self.controller,
            self.event_bus,
            self.executor,
            font_name=_register_cjk_font(),
        )
        return self.desktop_root

    def on_stop(self) -> None:
        desktop_root = getattr(self, "desktop_root", None)
        if desktop_root is not None:
            try:
                desktop_root.close()
            except Exception:
                logger.exception("Failed to close desktop UI")

        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            try:
                scheduler.shutdown()
            except Exception:
                logger.exception("Failed to shut down recording scheduler")

        executor = getattr(self, "executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logger.exception("Failed to shut down desktop executor")


def main() -> None:
    LuboDesktopApp().run()


if __name__ == "__main__":
    main()
