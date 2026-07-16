import importlib
import sys
import unittest
from types import ModuleType

from lubo.apps.desktop.preview import PreviewState, PreviewUpdate
from lubo.apps.desktop.pyav_decoder import DecodedFrame


FRAME_640_360 = DecodedFrame(
    width=640,
    height=360,
    rgba=b"\x01\x02\x03\xff" * (640 * 360),
)
FRAME_320_180 = DecodedFrame(
    width=320,
    height=180,
    rgba=b"\x04\x05\x06\xff" * (320 * 180),
)


class FakeWidget:
    def __init__(self, **kwargs):
        self._bindings = {}
        self.children = []
        self.parent = None
        self.width = 100.0
        self.height = 100.0
        self.x = 0.0
        self.y = 0.0
        self.size_hint = (1, 1)
        self.size_hint_x = 1
        self.size_hint_y = 1
        self.opacity = 1
        self.disabled = False
        for name, value in kwargs.items():
            setattr(self, name, value)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "size":
            object.__setattr__(self, "width", value[0])
            object.__setattr__(self, "height", value[1])
        elif name == "pos":
            object.__setattr__(self, "x", value[0])
            object.__setattr__(self, "y", value[1])

        bindings = self.__dict__.get("_bindings", {})
        for callback in tuple(bindings.get(name, ())):
            callback(self, value)

    @property
    def size(self):
        return (self.width, self.height)

    @size.setter
    def size(self, value):
        object.__setattr__(self, "width", value[0])
        object.__setattr__(self, "height", value[1])

    @property
    def pos(self):
        return (self.x, self.y)

    @pos.setter
    def pos(self, value):
        object.__setattr__(self, "x", value[0])
        object.__setattr__(self, "y", value[1])

    def add_widget(self, widget):
        widget.parent = self
        self.children.insert(0, widget)

    def bind(self, **bindings):
        for name, callback in bindings.items():
            self._bindings.setdefault(name, []).append(callback)


class FakeButton(FakeWidget):
    def trigger_action(self):
        for event_name in ("on_press", "on_release"):
            for callback in tuple(self._bindings.get(event_name, ())):
                callback(self)


class FakeTexture:
    created = []

    def __init__(self, size, colorfmt):
        self.size = tuple(size)
        self.colorfmt = colorfmt
        self.flip_vertical_calls = 0
        self.blit_calls = []
        type(self).created.append(self)

    @classmethod
    def create(cls, *, size, colorfmt):
        return cls(size, colorfmt)

    def flip_vertical(self):
        self.flip_vertical_calls += 1

    def blit_buffer(self, data, *, colorfmt, bufferfmt):
        self.blit_calls.append((data, colorfmt, bufferfmt))


def import_preview_pane():
    modules = {
        "kivy": ModuleType("kivy"),
        "kivy.graphics": ModuleType("kivy.graphics"),
        "kivy.graphics.texture": ModuleType("kivy.graphics.texture"),
        "kivy.uix": ModuleType("kivy.uix"),
        "kivy.uix.boxlayout": ModuleType("kivy.uix.boxlayout"),
        "kivy.uix.button": ModuleType("kivy.uix.button"),
        "kivy.uix.floatlayout": ModuleType("kivy.uix.floatlayout"),
        "kivy.uix.image": ModuleType("kivy.uix.image"),
        "kivy.uix.label": ModuleType("kivy.uix.label"),
    }
    modules["kivy.graphics.texture"].Texture = FakeTexture
    modules["kivy.uix.boxlayout"].BoxLayout = FakeWidget
    modules["kivy.uix.button"].Button = FakeButton
    modules["kivy.uix.floatlayout"].FloatLayout = FakeWidget
    modules["kivy.uix.image"].Image = FakeWidget
    modules["kivy.uix.label"].Label = FakeWidget

    module_name = "lubo.apps.desktop.preview_widget"
    tracked_names = (module_name, *modules)
    saved_modules = {
        name: sys.modules[name]
        for name in tracked_names
        if name in sys.modules
    }
    try:
        for name in tracked_names:
            sys.modules.pop(name, None)
        sys.modules.update(modules)
        return importlib.import_module(module_name).PreviewPane
    finally:
        for name in tracked_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


PreviewPane = import_preview_pane()


class PreviewPaneTests(unittest.TestCase):
    def setUp(self):
        FakeTexture.created.clear()

    def test_preview_area_stays_16_by_9_inside_wide_and_tall_hosts(self):
        pane = PreviewPane(font_name=None)

        pane.preview_host.size = (1000, 400)
        self.assertAlmostEqual(pane.preview_area.width, 400 * 16 / 9)
        self.assertAlmostEqual(pane.preview_area.height, 400)
        self.assertAlmostEqual(
            pane.preview_area.width / pane.preview_area.height,
            pane.preview_aspect_ratio,
        )

        pane.preview_host.size = (400, 600)
        self.assertAlmostEqual(pane.preview_area.width, 400)
        self.assertAlmostEqual(pane.preview_area.height, 400 * 9 / 16)
        self.assertAlmostEqual(pane.preview_aspect_ratio, 16 / 9)

    def test_header_is_compact_and_preview_widgets_are_layered(self):
        pane = PreviewPane()

        self.assertIsNone(pane.title_bar.size_hint_y)
        self.assertLessEqual(pane.title_bar.height, 70)
        self.assertIn(pane.title_controls, pane.title_bar.children)
        self.assertIs(pane.metadata_label.parent, pane.title_bar)
        self.assertIsNone(pane.metadata_label.size_hint_y)
        self.assertTrue(pane.metadata_label.shorten)
        self.assertTrue(pane.title_label.shorten)
        self.assertTrue(pane.connection_label.shorten)
        self.assertTrue(pane.mute_label.shorten)
        self.assertLessEqual(pane.title_label.font_size, 14)
        self.assertIn(pane.preview_area, pane.preview_host.children)
        self.assertIn(pane.video, pane.preview_area.children)
        self.assertIn(pane.status_label, pane.preview_area.children)

    def test_video_uses_contain_mode_to_scale_up_low_resolution_frames(self):
        pane = PreviewPane()

        self.assertEqual(pane.video.fit_mode, "contain")

    def test_playing_connection_state_remains_visible_in_stable_title_bar(self):
        pane = PreviewPane()

        pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )

        self.assertEqual(pane.connection_label.text, "Playing")
        self.assertEqual(pane.connection_label.opacity, 1)
        self.assertIsNone(pane.connection_label.size_hint_x)
        self.assertEqual(pane.connection_label.width, 88)
        self.assertIs(pane.connection_label.parent, pane.title_controls)
        self.assertEqual(pane.status_label.opacity, 0)
        self.assertLessEqual(pane.title_bar.height, 70)

    def test_prepare_switch_and_show_stopped_clear_without_advancing_generation(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(4, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )

        pane.prepare_switch()

        self.assertEqual(pane.generation, 4)
        self.assertEqual(pane.target_id, "target")
        self.assertIsNone(pane.video.texture)
        self.assertEqual(pane.connection_label.text, "Resolving")
        self.assertEqual(pane.status_label.text, "Preparing preview...")
        self.assertEqual(pane.status_label.opacity, 1)

        pane.show_state(PreviewState.STOPPED)

        self.assertEqual(pane.generation, 4)
        self.assertEqual(pane.target_id, "target")
        self.assertIsNone(pane.video.texture)
        self.assertEqual(pane.connection_label.text, "Stopped")
        self.assertEqual(pane.status_label.text, "Preview stopped")
        self.assertEqual(pane.status_label.opacity, 1)

    def test_frame_creates_rgba_texture_and_flips_it_once(self):
        pane = PreviewPane()

        accepted = pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )

        self.assertTrue(accepted)
        texture = pane.video.texture
        self.assertEqual(texture.size, (640, 360))
        self.assertEqual(texture.colorfmt, "rgba")
        self.assertEqual(texture.flip_vertical_calls, 1)
        self.assertEqual(
            texture.blit_calls,
            [(FRAME_640_360.rgba, "rgba", "ubyte")],
        )
        self.assertEqual(pane.video.color, [1, 1, 1, 1])
        self.assertEqual(pane.preview_aspect_ratio, 16 / 9)

    def test_same_size_frames_reuse_texture_without_flipping_again(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )
        original = pane.video.texture

        pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )

        self.assertIs(pane.video.texture, original)
        self.assertEqual(len(FakeTexture.created), 1)
        self.assertEqual(original.flip_vertical_calls, 1)
        self.assertEqual(len(original.blit_calls), 2)

    def test_frame_size_change_replaces_texture(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
        )
        original = pane.video.texture

        pane.apply_update(
            PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_320_180)
        )

        self.assertIsNot(pane.video.texture, original)
        self.assertEqual(pane.video.texture.size, (320, 180))
        self.assertEqual(pane.video.texture.flip_vertical_calls, 1)

    def test_stale_generation_cannot_replace_or_clear_current_frame(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(4, "current", PreviewState.PLAYING, frame=FRAME_640_360)
        )
        current_texture = pane.video.texture

        accepted = pane.apply_update(
            PreviewUpdate(
                3,
                "old",
                PreviewState.FAILED,
                message="https://stream.example/secret?token=abc",
            )
        )

        self.assertFalse(accepted)
        self.assertIs(pane.video.texture, current_texture)
        self.assertEqual(pane.generation, 4)
        self.assertEqual(pane.target_id, "current")

    def test_same_generation_from_different_target_cannot_change_current_view(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(7, "current", PreviewState.PLAYING, frame=FRAME_640_360)
        )
        current_texture = pane.video.texture
        current_status = pane.status_label.text
        current_status_opacity = pane.status_label.opacity

        accepted = pane.apply_update(
            PreviewUpdate(
                7,
                "different",
                PreviewState.FAILED,
                message="https://stream.example/secret?token=abc",
            )
        )

        self.assertFalse(accepted)
        self.assertIs(pane.video.texture, current_texture)
        self.assertEqual(pane.status_label.text, current_status)
        self.assertEqual(pane.status_label.opacity, current_status_opacity)
        self.assertEqual(pane.video.color, [1, 1, 1, 1])
        self.assertEqual(pane.generation, 7)
        self.assertEqual(pane.target_id, "current")

    def test_new_generation_clears_previous_target_before_showing_state(self):
        pane = PreviewPane()
        pane.apply_update(
            PreviewUpdate(1, "old", PreviewState.PLAYING, frame=FRAME_640_360)
        )

        pane.apply_update(
            PreviewUpdate(2, "new", PreviewState.RESOLVING, message="signed-url")
        )

        self.assertIsNone(pane.video.texture)
        self.assertEqual(pane.video.color, [0, 0, 0, 1])
        self.assertEqual(pane.status_label.text, "Preparing preview...")
        self.assertEqual(pane.target_id, "new")

    def test_terminal_states_clear_frame_and_use_generic_safe_text(self):
        expected = {
            PreviewState.STOPPED: "Preview stopped",
            PreviewState.OFFLINE: "Stream offline",
            PreviewState.FAILED: "Preview unavailable",
        }
        secret_url = "https://live.example/video.flv?token=top-secret"

        for index, (state, text) in enumerate(expected.items(), start=1):
            with self.subTest(state=state):
                pane = PreviewPane()
                pane.apply_update(
                    PreviewUpdate(
                        index,
                        "target",
                        PreviewState.PLAYING,
                        frame=FRAME_640_360,
                    )
                )
                pane.apply_update(
                    PreviewUpdate(index, "target", state, message=secret_url)
                )

                self.assertIsNone(pane.video.texture)
                self.assertEqual(pane.status_label.text, text)
                visible_text = " ".join(
                    widget.text
                    for widget in (
                        pane.title_label,
                        pane.connection_label,
                        pane.metadata_label,
                        pane.mute_label,
                        pane.status_label,
                        pane.stop_button,
                    )
                )
                self.assertNotIn(secret_url, visible_text)
                self.assertNotIn("top-secret", visible_text)

    def test_platform_and_room_note_metadata_are_visible_without_target_url(self):
        pane = PreviewPane(platform="Douyin", room_note="Evening room")

        self.assertEqual(
            pane.metadata_label.text,
            "Platform: Douyin | Room: Evening room",
        )
        pane.set_metadata(
            platform="Huya",
            room_note="https://secret.example/live?token=abc",
        )
        self.assertIn("Platform: Huya", pane.metadata_label.text)
        self.assertNotIn("https://", pane.metadata_label.text)
        self.assertNotIn("token", pane.metadata_label.text)

    def test_quality_metadata_is_optional_visible_and_sanitized(self):
        pane = PreviewPane(
            platform="Douyin",
            room_note="Evening room",
            quality="Original",
        )

        self.assertEqual(
            pane.metadata_label.text,
            "Platform: Douyin | Room: Evening room | Quality: Original",
        )

        pane.set_target_metadata(
            platform="Huya",
            room_note="Night room",
            quality="https://secret.example/live?token=quality-secret",
        )

        self.assertIn("Quality: Unknown", pane.metadata_label.text)
        self.assertNotIn("https://", pane.metadata_label.text)
        self.assertNotIn("quality-secret", pane.metadata_label.text)

    def test_preview_is_muted_by_default(self):
        pane = PreviewPane()

        self.assertTrue(pane.muted)
        self.assertEqual(pane.mute_label.text, "Muted")

    def test_stop_button_only_calls_preview_stop_callback(self):
        stopped = []
        pane = PreviewPane(on_stop=lambda: stopped.append("preview"))

        pane.stop_button.trigger_action()

        self.assertEqual(stopped, ["preview"])


if __name__ == "__main__":
    unittest.main()
