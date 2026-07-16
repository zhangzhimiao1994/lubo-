from __future__ import annotations

import asyncio
import copy
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any


def _configure_kivy_environment(platform: str) -> None:
    if platform != "win32":
        return

    # Kivy 2.3.1 can restore a Win32 input callback after its window is gone.
    os.environ["KCFG_INPUT_WM_PEN"] = ""
    os.environ["KCFG_INPUT_WM_TOUCH"] = ""


_configure_kivy_environment(sys.platform)

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from lubo.apps.desktop.controller import DesktopController
from lubo.apps.desktop.preview import PreviewSession, PreviewState, PreviewUpdate
from lubo.apps.desktop.preview_widget import PreviewPane
from lubo.apps.desktop.pyav_decoder import PyAvDecoder
from lubo.apps.desktop.runtime import resolve_ffmpeg
from lubo.core.config import ConfigService
from lubo.core.events import EventBus, RecorderEvent, RecorderEventType
from lubo.core.models import Quality, RecordingStatus, RecordingTarget
from lubo.core.scheduler import RecordingScheduler, SchedulerConfig
from lubo.platforms.factory import build_default_registry
from lubo.recorders.ffmpeg import FFmpegRecorder


logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 60
_QUEUE_SENTINEL = object()
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_URL_PATTERN = re.compile(
    r"\b(?:https?|flv|hls|rtmps?)://\S+",
    re.IGNORECASE,
)
_AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(\bauthorization\s*[:=]\s*bearer\s+)[^\s,;]+",
    re.IGNORECASE,
)
_COOKIE_HEADER_PATTERN = re.compile(
    r"(\bcookie\s*[:=]\s*)[^\r\n]+",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERN = re.compile(
    r"\b(access[_-]?token|refresh[_-]?token|id[_-]?token|token|"
    r"sign(?:ature)?|auth(?:entication)?|session(?:id)?)"
    r"\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _sanitize_log_message(super().format(copy.copy(record)))


def _configure_file_logging(data_dir: Path) -> RotatingFileHandler:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "lubo.log",
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        _SanitizingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    )
    package_logger = logging.getLogger("lubo")
    package_logger.setLevel(logging.INFO)
    package_logger.addHandler(handler)
    return handler


def _close_file_logging(handler: logging.Handler) -> None:
    package_logger = logging.getLogger("lubo")
    package_logger.removeHandler(handler)
    handler.close()


def _sanitize_log_message(message: str) -> str:
    sanitized = _URL_PATTERN.sub("<redacted-url>", message)
    sanitized = _AUTHORIZATION_BEARER_PATTERN.sub(r"\1<redacted>", sanitized)
    sanitized = _COOKIE_HEADER_PATTERN.sub(r"\1<redacted>", sanitized)
    return _SECRET_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        sanitized,
    )


def _log_recorder_event(event: RecorderEvent) -> None:
    level = (
        logging.ERROR
        if event.type in {RecorderEventType.ERROR, RecorderEventType.RECORDING_FAILED}
        else logging.INFO
    )
    logger.log(
        level,
        "event=%s target=%s message=%s",
        event.type.value,
        event.target_id,
        _sanitize_log_message(event.message),
    )


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
        preview_session: PreviewSession,
        target_action_executor: DaemonTaskQueue,
        preview_executor: DaemonTaskQueue,
        font_name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(orientation="vertical", spacing=8, padding=10, **kwargs)
        self.controller = controller
        self.event_bus = event_bus
        self.executor = executor
        self.preview_session = preview_session
        self.target_action_executor = target_action_executor
        self.preview_executor = preview_executor
        self.font_name = font_name
        self._use_cjk = font_name is not None
        self._watch_event = None
        self._check_future: Future[None] | None = None
        self._stop_future: Future[None] | None = None
        self._target_futures: dict[str, Future[None]] = {}
        self._target_rows: dict[str, BoxLayout] = {}
        self._preview_generation: int | None = None
        self._preview_target_id: str | None = None
        self._preview_request_id = 0
        self._preview_action_future: Future[Any] | None = None
        self._preview_log_generation: int | None = None
        self._preview_log_state: PreviewState | None = None
        self._stopping = False
        self._closing = False

        self.input_controls = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=42,
            spacing=8,
        )
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
        self.input_controls.add_widget(self.url_input)
        self.input_controls.add_widget(self.name_input)
        self.add_widget(self.input_controls)

        self.global_controls = BoxLayout(size_hint_y=None, height=44, spacing=8)
        self.add_button = Button(
            text=self._text("添加", "Add"),
            font_name=font_name or "Roboto",
        )
        self.add_button.bind(on_press=self._add_target)
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
        self.global_controls.add_widget(self.add_button)
        self.global_controls.add_widget(self.watch_button)
        self.global_controls.add_widget(self.stop_button)
        self.add_widget(self.global_controls)

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

        self.main_content = BoxLayout(
            orientation="horizontal",
            spacing=10,
            size_hint_y=0.68,
        )
        self.target_section = BoxLayout(
            orientation="vertical",
            spacing=4,
            size_hint_x=0.42,
            size_hint_min_x=300,
        )
        self.target_section.add_widget(
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
        self.target_scroll = ScrollView()
        self.target_scroll.add_widget(self.target_list)
        self.target_section.add_widget(self.target_scroll)
        self.preview_pane = PreviewPane(
            on_stop=self._stop_preview,
            font_name=font_name,
            size_hint_x=0.58,
            size_hint_min_x=440,
        )
        self.main_content.add_widget(self.target_section)
        self.main_content.add_widget(self.preview_pane)
        self.add_widget(self.main_content)

        self.log_section = BoxLayout(
            orientation="vertical",
            spacing=4,
            size_hint_y=0.32,
        )
        self.log_section.add_widget(
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
        self.log_scroll = ScrollView()
        self.log_scroll.add_widget(self.log)
        self.log_section.add_widget(self.log_scroll)
        self.add_widget(self.log_section)

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
        if self._closing:
            return
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
            self.status.text = self._safe_error_text(prefix, exc)
            self._append_log(self.status.text)
            return
        self.url_input.text = ""
        self.name_input.text = ""
        self.status.text = self._text("目标已添加", "Target added")
        self._render_targets()

    def _toggle_watch(self, _button) -> None:
        if self._closing:
            return
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
            fallback = self._text(
                f"检查超时（{CHECK_TIMEOUT_SECONDS} 秒）",
                f"Timed out after {CHECK_TIMEOUT_SECONDS} seconds",
            )
            self.status.text = self._safe_error_text(prefix, exc, fallback)
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
            self.status.text = self._safe_error_text(prefix, exc)
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
            self.status.text = self._safe_error_text(prefix, exc)
            self._append_log(self.status.text)
        else:
            self.status.text = self._text(
                "已停止全部录制",
                "All recordings stopped",
            )

    def _on_event(self, event: RecorderEvent) -> None:
        if not self._closing:
            Clock.schedule_once(lambda _dt, item=event: self._show_event(item), 0)

    def _show_event(self, event: RecorderEvent) -> None:
        if self._closing:
            return
        event_names = EVENT_NAMES_ZH if self._use_cjk else EVENT_NAMES_EN
        event_name = event_names.get(event.type.value, event.type.value)
        separator = "：" if self._use_cjk else ": "
        safe_message = _sanitize_log_message(event.message)
        detail = f"{separator}{safe_message}" if safe_message else ""
        timestamp = datetime.fromtimestamp(event.created_at).strftime("%H:%M:%S")
        self._append_log(f"[{timestamp}] {event_name} {event.target_id}{detail}")

    def _append_log(self, text: str) -> None:
        lines = [*self.log.text.splitlines(), _sanitize_log_message(text)]
        self.log.text = "\n".join(lines[-500:])

    def _render_targets(self) -> None:
        self.target_list.clear_widgets()
        self._target_rows = {}
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
            row = BoxLayout(
                orientation="vertical",
                spacing=3,
                size_hint_y=None,
                height=112,
            )
            row.enabled = target.enabled
            row.identity_label = Label(
                text=self._target_identity(target),
                font_name=self.font_name or "Roboto",
                size_hint_y=None,
                height=22,
                halign="left",
                valign="middle",
                shorten=True,
                shorten_from="right",
                max_lines=1,
            )
            row.identity_label.bind(size=self._fit_target_label)
            row.url_label = Label(
                text=target.url,
                font_name=self.font_name or "Roboto",
                size_hint_y=None,
                height=20,
                halign="left",
                valign="middle",
                shorten=True,
                shorten_from="right",
                max_lines=1,
            )
            row.url_label.bind(size=self._fit_target_label)
            row.state_label = Label(
                text=self._target_state_text(target),
                font_name=self.font_name or "Roboto",
                size_hint_y=None,
                height=20,
                halign="left",
                valign="middle",
                shorten=True,
                shorten_from="right",
                max_lines=1,
            )
            row.state_label.bind(size=self._fit_target_label)
            actions = BoxLayout(orientation="horizontal", spacing=4, height=34)
            row.preview_button = Button(
                text=self._text("预览", "Preview"),
                font_name=self.font_name or "Roboto",
            )
            row.pause_button = Button(
                text=self._text("停止并暂停", "Stop and pause"),
                font_name=self.font_name or "Roboto",
            )
            row.resume_button = Button(
                text=self._text("恢复", "Resume"),
                font_name=self.font_name or "Roboto",
            )
            row.preview_button.bind(
                on_press=lambda _button, target_id=target.id: self._start_preview(
                    target_id
                )
            )
            row.pause_button.bind(
                on_press=lambda _button, target_id=target.id: self._submit_target_action(
                    target_id,
                    "pause",
                )
            )
            row.resume_button.bind(
                on_press=lambda _button, target_id=target.id: self._submit_target_action(
                    target_id,
                    "resume",
                )
            )
            pending = target.id in self._target_futures
            row.preview_button.disabled = pending
            row.pause_button.disabled = pending or not target.enabled
            row.resume_button.disabled = pending or target.enabled
            actions.add_widget(row.preview_button)
            actions.add_widget(row.pause_button)
            actions.add_widget(row.resume_button)
            row.add_widget(row.identity_label)
            row.add_widget(row.url_label)
            row.add_widget(row.state_label)
            row.add_widget(actions)
            self._target_rows[target.id] = row
            self.target_list.add_widget(row)

    def _target_identity(self, target: RecordingTarget) -> str:
        platform = self._preview_platform(target)
        note = self._safe_metadata(
            target.display_name,
            self._text("未备注", "No note"),
        )
        return self._text(
            f"平台: {platform} | 备注: {note}",
            f"Platform: {platform} | Name: {note}",
        )

    def _target_states(self, target: RecordingTarget) -> tuple[str, str]:
        if not target.enabled:
            return (
                self._text("未知", "Unknown"),
                self._text("已暂停", "Paused"),
            )
        try:
            task = self.controller.scheduler.tasks.get(target.id)
        except Exception:
            task = None

        stream = getattr(task, "stream", None) if task is not None else None
        if stream is None:
            live_state = self._text("未知", "Unknown")
        elif stream.is_live:
            live_state = self._text("直播中", "Live")
        else:
            live_state = self._text("离线", "Offline")

        status = getattr(task, "status", RecordingStatus.IDLE)
        recording_names = {
            RecordingStatus.RESOLVING: self._text("检查中", "Checking"),
            RecordingStatus.RECORDING: self._text("录制中", "Recording"),
            RecordingStatus.STOPPING: self._text("停止中", "Stopping"),
            RecordingStatus.ERROR: self._text("错误", "Error"),
        }
        recording_state = recording_names.get(
            status,
            self._text("空闲", "Idle"),
        )
        return live_state, recording_state

    def _target_state_text(self, target: RecordingTarget) -> str:
        live_state, recording_state = self._target_states(target)
        return self._text(
            f"直播: {live_state} | 录制: {recording_state}",
            f"Live: {live_state} | Recording: {recording_state}",
        )

    def _start_preview(self, target_id: str) -> None:
        if self._closing or target_id in self._target_futures:
            return
        target = self.controller.target_by_id(target_id)
        if target is None:
            return
        self._preview_request_id += 1
        request_id = self._preview_request_id
        self._preview_generation = None
        self._preview_target_id = target.id
        self._reset_preview_state_log()
        self.preview_pane.set_target_metadata(
            platform=self._preview_platform(target),
            room_note=self._safe_metadata(
                target.display_name,
                self._text("所选目标", "Selected target"),
            ),
            quality=self._quality_text(target.quality),
        )
        self.preview_pane.prepare_switch()
        try:
            future = self._submit_latest_preview_action(
                lambda: self.preview_session.start(
                    target,
                    lambda update: self._on_preview_update(
                        request_id,
                        update,
                    ),
                )
            )
        except Exception:
            self._preview_request_id += 1
            self._preview_generation = None
            self._preview_target_id = None
            self._preview_action_future = None
            self._reset_preview_state_log()
            self.preview_pane.show_state(PreviewState.FAILED)
            self._show_preview_failure(
                self._text("预览启动失败", "Preview failed to start")
            )
            return
        future.add_done_callback(
            lambda item: self._schedule_preview_start_result(
                request_id,
                target.id,
                item,
            )
        )

    def _preview_platform(self, target: RecordingTarget) -> str:
        value = target.platform_key
        if not value:
            try:
                adapter = self.controller.scheduler.registry.match(target.url)
                value = getattr(adapter, "display_name", "") if adapter else ""
            except Exception:
                value = ""
        return self._safe_metadata(value, self._text("未知", "Unknown"))

    def _quality_text(self, quality: Quality) -> str:
        names = {
            Quality.ORIGINAL: self._text("原画", "Original"),
            Quality.BLUE_RAY: self._text("蓝光", "Blue-ray"),
            Quality.ULTRA: self._text("超清", "Ultra"),
            Quality.HIGH: self._text("高清", "High"),
            Quality.STANDARD: self._text("标清", "Standard"),
            Quality.SMOOTH: self._text("流畅", "Smooth"),
        }
        return self._safe_metadata(
            names.get(quality, ""),
            self._text("未知", "Unknown"),
        )

    @staticmethod
    def _safe_metadata(value: str, fallback: str) -> str:
        compact = " ".join(str(value or "").split())
        if not compact or _sanitize_log_message(compact) != compact:
            return fallback
        return compact

    def _on_preview_update(self, request_id: int, update: Any) -> None:
        if self._closing:
            return
        Clock.schedule_once(
            lambda _dt, item=update: self._apply_preview_update(
                request_id,
                item,
            ),
            0,
        )

    def _apply_preview_update(self, request_id: int, update: Any) -> None:
        if self._closing or request_id != self._preview_request_id:
            return
        if update.target_id != self._preview_target_id:
            return
        if self._preview_generation is None:
            self._preview_generation = update.generation
        elif update.generation != self._preview_generation:
            return
        self._log_preview_state(update)
        self.preview_pane.apply_update(update)

    def _schedule_preview_start_result(
        self,
        request_id: int,
        target_id: str,
        future: Future[int],
    ) -> None:
        if not self._closing:
            Clock.schedule_once(
                lambda _dt: self._finish_preview_start(
                    request_id,
                    target_id,
                    future,
                ),
                0,
            )

    def _finish_preview_start(
        self,
        request_id: int,
        target_id: str,
        future: Future[int],
    ) -> None:
        if not self._clear_preview_action_future(future):
            return
        if self._closing or request_id != self._preview_request_id:
            return
        try:
            generation = future.result()
        except Exception:
            self._preview_request_id += 1
            self._preview_generation, self._preview_target_id = None, None
            self._reset_preview_state_log()
            self.preview_pane.show_state(PreviewState.FAILED)
            self._show_preview_failure(
                self._text("预览启动失败", "Preview failed to start")
            )
            return
        if target_id != self._preview_target_id:
            return
        if self._preview_generation is None:
            self._preview_generation = generation
        elif self._preview_generation != generation:
            self._preview_request_id += 1
            self._preview_generation, self._preview_target_id = None, None
            self.preview_pane.show_state(PreviewState.FAILED)
            self._show_preview_failure(
                self._text("预览启动失败", "Preview failed to start")
            )

    def _log_preview_state(self, update: PreviewUpdate) -> None:
        if (
            self._preview_log_generation == update.generation
            and self._preview_log_state is update.state
        ):
            return
        self._preview_log_generation = update.generation
        self._preview_log_state = update.state
        level = logging.ERROR if update.state is PreviewState.FAILED else logging.INFO
        logger.log(
            level,
            "event=preview_state target=%s state=%s message=%s",
            update.target_id,
            update.state.value,
            _sanitize_log_message(update.message) or "-",
        )

    def _reset_preview_state_log(self) -> None:
        self._preview_log_generation = None
        self._preview_log_state = None

    def _stop_preview(self) -> None:
        if self._closing:
            return
        generation = self._preview_generation
        target_id = self._preview_target_id
        self._preview_request_id += 1
        request_id = self._preview_request_id
        self._preview_generation, self._preview_target_id = None, None
        if generation is not None and target_id is not None:
            stopped = PreviewUpdate(
                generation=generation,
                target_id=target_id,
                state=PreviewState.STOPPED,
            )
            self._log_preview_state(stopped)
        self._reset_preview_state_log()
        self.preview_pane.show_state(PreviewState.STOPPED)
        self._submit_preview_stop(request_id)

    def _submit_preview_stop(
        self,
        request_id: int,
        *,
        notify: bool = True,
    ) -> None:
        try:
            future = self._submit_latest_preview_action(
                self.preview_session.stop
            )
        except Exception:
            if notify and not self._closing:
                self._show_preview_failure(
                    self._text("预览停止失败", "Preview failed to stop")
                )
            return
        if notify:
            future.add_done_callback(
                lambda item: self._schedule_preview_stop_result(
                    request_id,
                    item,
                )
            )
        else:
            future.add_done_callback(self._clear_preview_action_future)

    def _schedule_preview_stop_result(
        self,
        request_id: int,
        future: Future[None],
    ) -> None:
        if not self._closing:
            Clock.schedule_once(
                lambda _dt: self._finish_preview_stop(request_id, future),
                0,
            )

    def _finish_preview_stop(
        self,
        request_id: int,
        future: Future[None],
    ) -> None:
        if not self._clear_preview_action_future(future):
            return
        if self._closing or request_id != self._preview_request_id:
            return
        try:
            future.result()
        except Exception:
            self._show_preview_failure(
                self._text("预览停止失败", "Preview failed to stop")
            )

    def _submit_latest_preview_action(
        self,
        operation: Callable[[], Any],
    ) -> Future[Any]:
        previous = self._preview_action_future
        if previous is not None and not previous.done():
            previous.cancel()
        future = self.preview_executor.submit(operation)
        self._preview_action_future = future
        return future

    def _clear_preview_action_future(self, future: Future[Any]) -> bool:
        if self._preview_action_future is not future:
            return False
        self._preview_action_future = None
        return True

    def _show_preview_failure(self, text: str) -> None:
        if self._closing:
            return
        self.status.text = text
        self._append_log(text)

    def _submit_target_action(self, target_id: str, action: str) -> None:
        if self._closing or target_id in self._target_futures:
            return
        target = self.controller.target_by_id(target_id)
        if target is None:
            return
        if action == "pause":
            if not target.enabled:
                return
            if self._preview_target_id == target_id:
                self._stop_preview()
            operation = lambda: self.controller.stop_and_pause_target(target_id)
            working = self._text("正在停止并暂停目标...", "Stopping and pausing target...")
        elif action == "resume":
            if target.enabled:
                return
            operation = lambda: self.controller.resume_target(target_id)
            working = self._text("正在恢复目标...", "Resuming target...")
        else:
            return
        self.status.text = working
        try:
            future = self.target_action_executor.submit(operation)
        except Exception:
            self._show_target_action_failure()
            return
        self._target_futures[target_id] = future
        self._set_target_row_busy(target_id, True)
        future.add_done_callback(
            lambda item, item_id=target_id, item_action=action: self._schedule_target_action_result(
                item_id,
                item_action,
                item,
            )
        )

    def _set_target_row_busy(self, target_id: str, busy: bool) -> None:
        row = self._target_rows.get(target_id)
        if row is None:
            return
        row.preview_button.disabled = busy
        row.pause_button.disabled = busy or not row.enabled
        row.resume_button.disabled = busy or row.enabled

    def _schedule_target_action_result(
        self,
        target_id: str,
        action: str,
        future: Future[None],
    ) -> None:
        if not self._closing:
            Clock.schedule_once(
                lambda _dt: self._finish_target_action(target_id, action, future),
                0,
            )

    def _finish_target_action(
        self,
        target_id: str,
        action: str,
        future: Future[None],
    ) -> None:
        if self._closing or self._target_futures.get(target_id) is not future:
            return
        self._target_futures.pop(target_id, None)
        try:
            future.result()
        except Exception:
            self._show_target_action_failure()
        else:
            self.status.text = (
                self._text("目标已停止并暂停", "Target stopped and paused")
                if action == "pause"
                else self._text("目标已恢复", "Target resumed")
            )
        self._render_targets()

    def _show_target_action_failure(self) -> None:
        self.status.text = self._text("目标操作失败", "Target action failed")
        self._append_log(self.status.text)

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

    @staticmethod
    def _safe_error_text(
        prefix: str,
        error: BaseException,
        fallback: str = "Operation failed",
    ) -> str:
        detail = str(error) or fallback
        return _sanitize_log_message(f"{prefix}{detail}")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._preview_request_id += 1
        self._preview_generation, self._preview_target_id = None, None
        self._reset_preview_state_log()
        self.stop_watch()
        self.event_bus.unsubscribe(self._on_event)
        self._submit_preview_stop(self._preview_request_id, notify=False)

    def queue_preview_close(self) -> Future[Any] | None:
        try:
            future = self._submit_latest_preview_action(
                self.preview_session.close
            )
        except Exception:
            logger.exception("Failed to queue desktop preview close")
            return None
        future.add_done_callback(self._clear_preview_action_future)
        return future


class LuboDesktopApp(App):
    def build(self) -> DesktopRoot:
        data_dir = Path(self.user_data_dir).expanduser().resolve()
        config_path, url_path = _prepare_user_config(data_dir)
        config_service = ConfigService(config_path)
        config = config_service.load()
        output_dir = _prepare_output_dir(data_dir, config.save_path)
        event_bus = EventBus()
        event_bus.subscribe(_log_recorder_event)
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
                minimum_free_space_mb=config.minimum_free_space_mb,
            ),
        )
        self.event_bus = event_bus
        self.controller = DesktopController(
            config_service,
            url_path,
            self.scheduler,
        )
        self.executor = DaemonTaskQueue(thread_name="recorder-check")
        self.target_action_executor = DaemonTaskQueue(
            thread_name="target-actions"
        )
        self.preview_executor = DaemonTaskQueue(
            thread_name="preview-actions"
        )
        self.preview_session = PreviewSession(
            resolver=self.scheduler.resolve_preview_stream,
            decoder_factory=PyAvDecoder,
        )
        self.desktop_root = DesktopRoot(
            self.controller,
            self.event_bus,
            self.executor,
            preview_session=self.preview_session,
            target_action_executor=self.target_action_executor,
            preview_executor=self.preview_executor,
            font_name=_register_cjk_font(),
        )
        return self.desktop_root

    def on_stop(self) -> None:
        if getattr(self, "_shutdown_started", False):
            return
        self._shutdown_started = True

        desktop_root = getattr(self, "desktop_root", None)
        if desktop_root is not None:
            try:
                desktop_root.close()
            except Exception:
                logger.exception("Failed to close desktop UI")

        preview_executor = getattr(self, "preview_executor", None)
        preview_session = getattr(self, "preview_session", None)
        preview_close_future: Future[None] | None = None
        queue_preview_close = getattr(
            desktop_root,
            "queue_preview_close",
            None,
        )
        if callable(queue_preview_close):
            preview_close_future = queue_preview_close()
        elif preview_executor is not None and preview_session is not None:
            try:
                preview_close_future = preview_executor.submit(
                    preview_session.close
                )
            except Exception:
                logger.exception("Failed to queue desktop preview close")
        if preview_executor is not None:
            try:
                preview_executor.shutdown(
                    wait=True,
                    cancel_futures=False,
                )
            except Exception:
                logger.exception("Failed to shut down preview executor")
        if preview_close_future is not None:
            try:
                preview_close_future.result()
            except Exception:
                logger.exception("Failed to close desktop preview")

        target_action_executor = getattr(self, "target_action_executor", None)
        if target_action_executor is not None:
            try:
                target_action_executor.shutdown(
                    wait=True,
                    cancel_futures=True,
                )
            except Exception:
                logger.exception("Failed to shut down target action executor")

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
    app = LuboDesktopApp()
    log_handler = _configure_file_logging(
        Path(app.user_data_dir).expanduser().resolve()
    )
    try:
        app.run()
    finally:
        _close_file_logging(log_handler)


if __name__ == "__main__":
    main()
