from __future__ import annotations

from collections.abc import Callable

from kivy.graphics.texture import Texture
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from lubo.apps.desktop.preview import PreviewState, PreviewUpdate
from lubo.apps.desktop.pyav_decoder import DecodedFrame


_ASPECT_RATIO = 16.0 / 9.0
_TERMINAL_STATES = {
    PreviewState.STOPPED,
    PreviewState.OFFLINE,
    PreviewState.FAILED,
}
_STATUS_TEXT = {
    PreviewState.IDLE: "No preview selected",
    PreviewState.RESOLVING: "Preparing preview...",
    PreviewState.CONNECTING: "Connecting preview...",
    PreviewState.PLAYING: "Playing",
    PreviewState.RETRYING: "Reconnecting preview...",
    PreviewState.OFFLINE: "Stream offline",
    PreviewState.FAILED: "Preview unavailable",
    PreviewState.STOPPED: "Preview stopped",
}
_CONNECTION_TEXT = {
    PreviewState.IDLE: "Idle",
    PreviewState.RESOLVING: "Resolving",
    PreviewState.CONNECTING: "Connecting",
    PreviewState.PLAYING: "Playing",
    PreviewState.RETRYING: "Retrying",
    PreviewState.OFFLINE: "Offline",
    PreviewState.FAILED: "Failed",
    PreviewState.STOPPED: "Stopped",
}
_SENSITIVE_MARKERS = (
    "http://",
    "https://",
    "rtmp://",
    "rtmps://",
    "token=",
    "sign=",
    "cookie",
    "authorization",
)


class PreviewPane(BoxLayout):
    """Kivy preview surface that never owns recording lifecycle state."""

    preview_aspect_ratio = _ASPECT_RATIO

    def __init__(
        self,
        *,
        on_stop: Callable[[], None] | None = None,
        font_name: str | None = None,
        platform: str = "",
        room_note: str = "",
        quality: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            orientation="vertical",
            spacing=6,
            padding=8,
            **kwargs,
        )
        self._on_stop = on_stop
        self._generation = -1
        self._target_id: str | None = None
        self._texture = None
        self.muted = True
        resolved_font = font_name or "Roboto"

        self.title_bar = BoxLayout(
            orientation="vertical",
            spacing=2,
            size_hint_y=None,
            height=64,
        )
        self.title_controls = BoxLayout(
            orientation="horizontal",
            spacing=6,
            size_hint_y=None,
            height=36,
        )
        self.title_label = Label(
            text="Preview",
            font_name=resolved_font,
            font_size=14,
            size_hint_x=None,
            width=72,
            halign="left",
            shorten=True,
            shorten_from="right",
        )
        self.metadata_label = Label(
            text="",
            font_name=resolved_font,
            font_size=14,
            size_hint_y=None,
            height=26,
            halign="left",
            shorten=True,
            shorten_from="right",
        )
        self.connection_label = Label(
            text=_CONNECTION_TEXT[PreviewState.IDLE],
            font_name=resolved_font,
            font_size=14,
            size_hint_x=None,
            width=88,
            halign="center",
            shorten=True,
            shorten_from="right",
        )
        self.mute_label = Label(
            text="Muted",
            font_name=resolved_font,
            font_size=14,
            size_hint_x=None,
            width=52,
            halign="center",
            shorten=True,
            shorten_from="right",
        )
        self.stop_button = Button(
            text="Stop preview",
            font_name=resolved_font,
            font_size=14,
            size_hint_x=None,
            width=112,
        )
        self.stop_button.bind(on_release=self._request_stop)
        self.title_controls.add_widget(self.title_label)
        self.title_controls.add_widget(self.connection_label)
        self.title_controls.add_widget(self.mute_label)
        self.title_controls.add_widget(self.stop_button)
        self.title_bar.add_widget(self.title_controls)
        self.title_bar.add_widget(self.metadata_label)
        self.add_widget(self.title_bar)

        self.preview_host = FloatLayout()
        self.preview_area = FloatLayout(size_hint=(None, None))
        self.video = Image(
            texture=None,
            color=[0, 0, 0, 1],
            fit_mode="contain",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        self.status_label = Label(
            text=_STATUS_TEXT[PreviewState.IDLE],
            font_name=resolved_font,
            halign="center",
            valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        self.status_overlay = self.status_label
        self.preview_area.add_widget(self.video)
        self.preview_area.add_widget(self.status_label)
        self.preview_host.add_widget(self.preview_area)
        self.preview_host.bind(size=self._fit_preview, pos=self._fit_preview)
        self.add_widget(self.preview_host)

        for label in (
            self.title_label,
            self.connection_label,
            self.mute_label,
        ):
            label.bind(size=self._fit_single_line_label)
        self.metadata_label.bind(size=self._fit_single_line_label)
        self.status_label.bind(size=self._fit_label_text)

        self.set_metadata(
            platform=platform,
            room_note=room_note,
            quality=quality,
        )
        self._fit_preview(self.preview_host)

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def target_id(self) -> str | None:
        return self._target_id

    def set_metadata(
        self,
        *,
        platform: str = "",
        room_note: str = "",
        quality: str = "",
    ) -> None:
        safe_platform = self._safe_metadata_value(platform, "Unknown")
        safe_room = self._safe_metadata_value(room_note, "Selected target")
        parts = [f"Platform: {safe_platform}", f"Room: {safe_room}"]
        if quality:
            safe_quality = self._safe_metadata_value(quality, "Unknown")
            parts.append(f"Quality: {safe_quality}")
        self.metadata_label.text = " | ".join(parts)

    def set_target_metadata(
        self,
        *,
        platform: str = "",
        room_note: str = "",
        quality: str = "",
    ) -> None:
        self.set_metadata(
            platform=platform,
            room_note=room_note,
            quality=quality,
        )

    def prepare_switch(self) -> None:
        self.show_state(PreviewState.RESOLVING)

    def show_state(self, state: PreviewState) -> None:
        """Show an immediate UI state without changing session generation."""
        self._clear_frame()
        self.connection_label.text = _CONNECTION_TEXT.get(
            state,
            _CONNECTION_TEXT[PreviewState.FAILED],
        )
        self.connection_label.opacity = 1
        self.status_label.text = _STATUS_TEXT.get(
            state,
            _STATUS_TEXT[PreviewState.FAILED],
        )
        self.status_label.opacity = 0 if state is PreviewState.PLAYING else 1

    def apply_update(self, update: PreviewUpdate) -> bool:
        """Apply an update on Kivy's main thread; callers must marshal via Clock."""
        if update.generation < self._generation:
            return False
        if (
            update.generation == self._generation
            and self._target_id is not None
            and update.target_id != self._target_id
        ):
            return False

        if update.generation > self._generation:
            self._clear_frame()
            self._generation = update.generation
            self._target_id = update.target_id
        elif self._target_id is None:
            self._target_id = update.target_id

        self.connection_label.text = _CONNECTION_TEXT.get(
            update.state,
            _CONNECTION_TEXT[PreviewState.FAILED],
        )
        self.connection_label.opacity = 1

        if update.state in _TERMINAL_STATES:
            self._clear_frame()

        if update.state is PreviewState.PLAYING:
            if update.frame is None or not self._show_frame(update.frame):
                self._clear_frame()
                self.connection_label.text = _CONNECTION_TEXT[PreviewState.FAILED]
                self.status_label.text = _STATUS_TEXT[PreviewState.FAILED]
                self.status_label.opacity = 1
                return True
            self.status_label.text = _STATUS_TEXT[PreviewState.PLAYING]
            self.status_label.opacity = 0
            return True

        self.status_label.text = _STATUS_TEXT.get(
            update.state,
            _STATUS_TEXT[PreviewState.FAILED],
        )
        self.status_label.opacity = 1
        return True

    def _show_frame(self, frame: DecodedFrame) -> bool:
        width = int(frame.width)
        height = int(frame.height)
        if width <= 0 or height <= 0 or len(frame.rgba) != width * height * 4:
            return False

        size = (width, height)
        texture = self._texture
        if texture is None or tuple(texture.size) != size:
            texture = Texture.create(size=size, colorfmt="rgba")
            texture.flip_vertical()
            self._texture = texture

        texture.blit_buffer(
            frame.rgba,
            colorfmt="rgba",
            bufferfmt="ubyte",
        )
        self.video.texture = texture
        self.video.color = [1, 1, 1, 1]
        return True

    def _clear_frame(self) -> None:
        self._texture = None
        self.video.texture = None
        self.video.color = [0, 0, 0, 1]

    def _fit_preview(self, widget, *_args) -> None:
        available_width = max(float(widget.width), 0.0)
        available_height = max(float(widget.height), 0.0)
        width = min(available_width, available_height * _ASPECT_RATIO)
        height = width / _ASPECT_RATIO if width else 0.0
        self.preview_area.size = (width, height)
        self.preview_area.pos = (
            float(widget.x) + (available_width - width) / 2.0,
            float(widget.y) + (available_height - height) / 2.0,
        )

    @staticmethod
    def _fit_label_text(widget, size) -> None:
        widget.text_size = size

    @staticmethod
    def _fit_single_line_label(widget, size) -> None:
        widget.text_size = (size[0], None)

    def _request_stop(self, _button) -> None:
        if self._on_stop is not None:
            self._on_stop()

    @staticmethod
    def _safe_metadata_value(value: str, fallback: str) -> str:
        compact = " ".join(str(value or "").split())
        lowered = compact.casefold()
        if not compact or any(marker in lowered for marker in _SENSITIVE_MARKERS):
            return fallback
        return compact
