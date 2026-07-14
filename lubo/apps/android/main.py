from __future__ import annotations

import shutil
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from lubo.apps.android.platform import (
    app_storage_root,
    request_runtime_permissions,
    request_service_stop,
    start_recorder_service,
)
from lubo.apps.android.state import read_status
from lubo.core.config import ConfigService
from lubo.core.url_store import UrlStore


def _resource_root() -> Path:
    return Path(__file__).resolve().parents[3]


def prepare_storage(root: Path) -> tuple[Path, Path]:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    bundled = _resource_root() / "config"
    for name in ("config.ini", "URL_config.ini"):
        destination = config_dir / name
        source = bundled / name
        if not destination.exists() and source.is_file():
            shutil.copy2(source, destination)
    return config_dir / "config.ini", config_dir / "URL_config.ini"


class RecorderMobileRoot(BoxLayout):
    def __init__(self, storage_root: Path, **kwargs) -> None:
        super().__init__(orientation="vertical", spacing=dp(8), padding=dp(16), **kwargs)
        self.storage_root = storage_root
        config_path, url_path = prepare_storage(storage_root)
        self.config = ConfigService(config_path).load()
        self.store = UrlStore(url_path, default_quality=self.config.quality)
        self.targets = self.store.load()

        title = Label(
            text="Lubo",
            size_hint_y=None,
            height=dp(44),
            font_size="22sp",
            halign="left",
            valign="middle",
        )
        title.bind(size=lambda widget, size: setattr(widget, "text_size", size))
        self.add_widget(title)

        self.url_input = TextInput(
            hint_text="Douyin live room URL",
            multiline=False,
            size_hint_y=None,
            height=dp(52),
        )
        self.add_widget(self.url_input)

        add_button = Button(text="Add target", size_hint_y=None, height=dp(48))
        add_button.bind(on_release=self._add_target)
        self.add_widget(add_button)

        controls = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        start_button = Button(text="Start monitoring")
        start_button.bind(on_release=self._start)
        stop_button = Button(text="Stop")
        stop_button.bind(on_release=self._stop)
        controls.add_widget(start_button)
        controls.add_widget(stop_button)
        self.add_widget(controls)

        self.status_label = Label(
            text="Stopped",
            size_hint_y=None,
            height=dp(56),
            halign="left",
            valign="middle",
        )
        self.status_label.bind(
            size=lambda widget, size: setattr(widget, "text_size", size)
        )
        self.add_widget(self.status_label)

        scroll = ScrollView()
        self.target_list = BoxLayout(
            orientation="vertical",
            spacing=dp(4),
            size_hint_y=None,
        )
        self.target_list.bind(minimum_height=self.target_list.setter("height"))
        scroll.add_widget(self.target_list)
        self.add_widget(scroll)
        self._refresh_targets()
        Clock.schedule_interval(self._refresh_status, 1)

    def _add_target(self, _button) -> None:
        url = self.url_input.text.strip()
        if not url:
            return
        candidate = self.store.add(self.targets, url)
        self.store.save(candidate)
        self.targets = candidate
        self.url_input.text = ""
        self._refresh_targets()

    def _start(self, _button) -> None:
        start_recorder_service(self.storage_root)
        self.status_label.text = "Starting foreground service..."

    def _stop(self, _button) -> None:
        request_service_stop(self.storage_root)
        self.status_label.text = "Stopping recordings..."

    def _refresh_targets(self) -> None:
        self.target_list.clear_widgets()
        for target in self.targets:
            label = Label(
                text=f"{target.quality.value}  {target.display_name or target.url}",
                size_hint_y=None,
                height=dp(44),
                halign="left",
                valign="middle",
                shorten=True,
            )
            label.bind(size=lambda widget, size: setattr(widget, "text_size", size))
            self.target_list.add_widget(label)

    def _refresh_status(self, _interval) -> None:
        status = read_status(self.storage_root / "service_status.json")
        active = status.get("active_recordings", 0)
        self.status_label.text = f"{status.get('message', 'Stopped')}  |  Recording: {active}"


class LuboAndroidApp(App):
    title = "Lubo"

    def build(self) -> RecorderMobileRoot:
        request_runtime_permissions()
        return RecorderMobileRoot(app_storage_root())


def main() -> None:
    LuboAndroidApp().run()


if __name__ == "__main__":
    main()
