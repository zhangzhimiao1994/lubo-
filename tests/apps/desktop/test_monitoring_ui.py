import importlib
import sys
import threading
import unittest
from concurrent.futures import Future
from types import ModuleType, SimpleNamespace

from lubo.apps.desktop.preview import PreviewState, PreviewUpdate
from lubo.core.events import RecorderEvent, RecorderEventType
from lubo.core.models import RecordingStatus, RecordingTarget


class FakeWidget:
    def __init__(self, **kwargs):
        self._bindings = {}
        self.children = []
        self.parent = None
        self.width = 100.0
        self.height = 100.0
        self.size_hint = (1, 1)
        self.size_hint_x = 1
        self.size_hint_y = 1
        self.disabled = False
        self.text = ""
        self.texture_size = (0, 0)
        for name, value in kwargs.items():
            setattr(self, name, value)

    def add_widget(self, widget):
        widget.parent = self
        self.children.insert(0, widget)

    def clear_widgets(self):
        for widget in self.children:
            widget.parent = None
        self.children.clear()

    def bind(self, **bindings):
        for name, callback in bindings.items():
            self._bindings.setdefault(name, []).append(callback)

    def setter(self, name):
        return lambda _widget, value: setattr(self, name, value)


class FakeButton(FakeWidget):
    def trigger_action(self):
        for event_name in ("on_press", "on_release"):
            for callback in tuple(self._bindings.get(event_name, ())):
                callback(self)


class FakeClock:
    once = []
    intervals = []

    @classmethod
    def reset(cls):
        cls.once.clear()
        cls.intervals.clear()

    @classmethod
    def schedule_once(cls, callback, timeout=0):
        event = SimpleNamespace(callback=callback, timeout=timeout, cancel=lambda: None)
        cls.once.append(event)
        return event

    @classmethod
    def schedule_interval(cls, callback, interval):
        event = SimpleNamespace(
            callback=callback,
            interval=interval,
            cancelled=False,
        )
        event.cancel = lambda: setattr(event, "cancelled", True)
        cls.intervals.append(event)
        return event

    @classmethod
    def flush(cls):
        pending, cls.once = cls.once, []
        for event in pending:
            event.callback(0)


class FakePreviewPane(FakeWidget):
    def __init__(self, *, on_stop=None, **kwargs):
        super().__init__(**kwargs)
        self.on_stop = on_stop
        self.updates = []
        self.metadata = []
        self.states = []

    def apply_update(self, update):
        self.updates.append(update)

    def set_target_metadata(self, *, platform="", room_note="", quality=""):
        self.metadata.append((platform, room_note, quality))

    def show_state(self, state):
        self.states.append(state)

    def prepare_switch(self):
        self.show_state(PreviewState.RESOLVING)


class FakePreviewSession:
    def __init__(self):
        self.starts = []
        self.stop_calls = 0
        self.close_calls = 0
        self.target_id = None
        self.start_threads = []
        self.stop_threads = []
        self.on_start = None
        self.start_error = None

    def start(self, target, callback):
        self.start_threads.append(threading.get_ident())
        if self.start_error is not None:
            raise self.start_error
        generation = len(self.starts) + 1
        self.starts.append((target, callback, generation))
        self.target_id = target.id
        if self.on_start is not None:
            callback(self.on_start(generation, target))
        return generation

    def stop(self):
        self.stop_threads.append(threading.get_ident())
        self.stop_calls += 1
        self.target_id = None

    def close(self):
        self.close_calls += 1
        self.stop()

    def emit(self, start_index, update):
        self.starts[start_index][1](update)


class FakeExecutor:
    def __init__(self):
        self.submissions = []
        self.shutdown_calls = []

    def submit(self, fn):
        future = Future()
        self.submissions.append((fn, future))
        return future

    def run(self, index=0, *, threaded=False):
        fn, future = self.submissions[index]
        if not future.set_running_or_notify_cancel():
            return future

        def invoke():
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)

        if threaded:
            worker = threading.Thread(target=invoke)
            worker.start()
            worker.join(2)
            if worker.is_alive():
                raise AssertionError("fake executor worker did not finish")
        else:
            invoke()
        return future

    def mark_running(self, index=0):
        _fn, future = self.submissions[index]
        self.assert_pending(future)
        future.set_running_or_notify_cancel()
        return future

    @staticmethod
    def assert_pending(future):
        if future.done() or future.running():
            raise AssertionError("future is not pending")

    def shutdown(self, *, wait=False, cancel_futures=False):
        self.shutdown_calls.append(
            {"wait": wait, "cancel_futures": cancel_futures}
        )
        if cancel_futures:
            for _fn, future in self.submissions:
                if not future.running() and not future.done():
                    future.cancel()
        for index, (_fn, future) in enumerate(self.submissions):
            if not future.running() and not future.done():
                self.run(index)


class FakeEventBus:
    def __init__(self):
        self.subscribers = []
        self.unsubscribers = []

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        self.unsubscribers.append(callback)


class FakeController:
    def __init__(self, targets):
        self.targets = targets
        self.config = SimpleNamespace(loop_seconds=30)
        self.scheduler = SimpleNamespace(tasks={})
        self.pause_calls = []
        self.resume_calls = []

    def target_by_id(self, target_id):
        return next((item for item in self.targets if item.id == target_id), None)

    def stop_and_pause_target(self, target_id):
        self.pause_calls.append(target_id)
        target = self.target_by_id(target_id)
        target.enabled = False

    def resume_target(self, target_id):
        self.resume_calls.append(target_id)
        target = self.target_by_id(target_id)
        target.enabled = True


def import_desktop_main():
    modules = {
        "kivy": ModuleType("kivy"),
        "kivy.app": ModuleType("kivy.app"),
        "kivy.clock": ModuleType("kivy.clock"),
        "kivy.core": ModuleType("kivy.core"),
        "kivy.core.text": ModuleType("kivy.core.text"),
        "kivy.uix": ModuleType("kivy.uix"),
        "kivy.uix.boxlayout": ModuleType("kivy.uix.boxlayout"),
        "kivy.uix.button": ModuleType("kivy.uix.button"),
        "kivy.uix.label": ModuleType("kivy.uix.label"),
        "kivy.uix.scrollview": ModuleType("kivy.uix.scrollview"),
        "kivy.uix.textinput": ModuleType("kivy.uix.textinput"),
        "lubo.apps.desktop.preview_widget": ModuleType(
            "lubo.apps.desktop.preview_widget"
        ),
    }
    modules["kivy.app"].App = FakeWidget
    modules["kivy.clock"].Clock = FakeClock
    modules["kivy.core.text"].LabelBase = SimpleNamespace(register=lambda **_kw: None)
    modules["kivy.uix.boxlayout"].BoxLayout = FakeWidget
    modules["kivy.uix.button"].Button = FakeButton
    modules["kivy.uix.label"].Label = FakeWidget
    modules["kivy.uix.scrollview"].ScrollView = FakeWidget
    modules["kivy.uix.textinput"].TextInput = FakeWidget
    modules["lubo.apps.desktop.preview_widget"].PreviewPane = FakePreviewPane

    module_name = "lubo.apps.desktop.main"
    package = importlib.import_module("lubo.apps.desktop")
    tracked = (module_name, *modules)
    saved = {name: sys.modules[name] for name in tracked if name in sys.modules}
    saved_main = package.__dict__.get("main")
    had_main = "main" in package.__dict__
    try:
        for name in tracked:
            sys.modules.pop(name, None)
        sys.modules.update(modules)
        package.__dict__.pop("main", None)
        return importlib.import_module(module_name)
    finally:
        for name in tracked:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        if had_main:
            package.main = saved_main
        else:
            package.__dict__.pop("main", None)


desktop_main = import_desktop_main()


class DesktopMonitoringUiTests(unittest.TestCase):
    def setUp(self):
        FakeClock.reset()
        self.enabled = RecordingTarget(
            "https://live.douyin.com/one",
            id="enabled",
            display_name="Room one",
            platform_key="douyin",
        )
        self.paused = RecordingTarget(
            "https://example.test/two",
            id="paused",
            display_name="Room two",
            enabled=False,
            platform_key="huya",
        )
        self.controller = FakeController([self.enabled, self.paused])
        self.controller.scheduler.tasks = {
            self.enabled.id: SimpleNamespace(
                status=RecordingStatus.RECORDING,
                stream=SimpleNamespace(is_live=True),
            )
        }
        self.events = FakeEventBus()
        self.executor = FakeExecutor()
        self.target_action_executor = FakeExecutor()
        self.preview_executor = FakeExecutor()
        self.preview = FakePreviewSession()
        self.root = desktop_main.DesktopRoot(
            self.controller,
            self.events,
            self.executor,
            preview_session=self.preview,
            target_action_executor=self.target_action_executor,
            preview_executor=self.preview_executor,
        )

    def test_layout_a_has_top_controls_split_main_and_log_below(self):
        self.assertEqual(self.root.orientation, "vertical")
        self.assertEqual(self.root.main_content.orientation, "horizontal")
        self.assertIn(self.root.target_section, self.root.main_content.children)
        self.assertIn(self.root.preview_pane, self.root.main_content.children)
        self.assertIs(self.root.target_list.parent, self.root.target_scroll)
        self.assertIs(self.root.target_scroll.parent, self.root.target_section)
        self.assertIs(self.root.log_scroll.parent, self.root.log_section)
        self.assertIs(self.root.main_content.parent, self.root)
        self.assertIs(self.root.log_section.parent, self.root)
        self.assertEqual(self.root.target_section.size_hint_min_x, 300)
        self.assertEqual(self.root.preview_pane.size_hint_x, 0.58)
        self.assertEqual(self.root.preview_pane.size_hint_min_x, 440)
        total_minimum = (
            self.root.target_section.size_hint_min_x
            + self.root.preview_pane.size_hint_min_x
            + self.root.main_content.spacing
            + 20
        )
        self.assertLessEqual(total_minimum, 800)

    def test_target_rows_show_platform_name_short_url_and_separate_states(self):
        enabled_row = self.root._target_rows["enabled"]
        paused_row = self.root._target_rows["paused"]

        self.assertIn("douyin", enabled_row.identity_label.text)
        self.assertIn("Room one", enabled_row.identity_label.text)
        self.assertEqual(enabled_row.url_label.text, self.enabled.url)
        self.assertTrue(enabled_row.url_label.shorten)
        self.assertEqual(enabled_row.url_label.shorten_from, "right")
        self.assertIn("Live: Live", enabled_row.state_label.text)
        self.assertIn("Recording: Recording", enabled_row.state_label.text)
        self.assertIsNone(enabled_row.size_hint_y)
        self.assertGreaterEqual(enabled_row.height, 100)
        self.assertEqual(enabled_row.preview_button.text, "Preview")
        self.assertEqual(enabled_row.pause_button.text, "Stop and pause")
        self.assertEqual(enabled_row.resume_button.text, "Resume")
        self.assertFalse(enabled_row.pause_button.disabled)
        self.assertTrue(enabled_row.resume_button.disabled)
        self.assertIn("Live: Unknown", paused_row.state_label.text)
        self.assertIn("Recording: Paused", paused_row.state_label.text)
        self.assertTrue(paused_row.pause_button.disabled)
        self.assertFalse(paused_row.resume_button.disabled)

    def test_live_and_recording_states_map_independently(self):
        unknown = RecordingTarget("https://example.test/unknown", id="unknown")
        offline = RecordingTarget("https://example.test/offline", id="offline")
        live_idle = RecordingTarget("https://example.test/live", id="live-idle")
        stopping = RecordingTarget("https://example.test/stop", id="stopping")
        failed = RecordingTarget("https://example.test/error", id="failed")
        resolving = RecordingTarget(
            "https://example.test/resolving",
            id="resolving",
        )
        self.controller.scheduler.tasks.update(
            {
                offline.id: SimpleNamespace(
                    status=RecordingStatus.IDLE,
                    stream=SimpleNamespace(is_live=False),
                ),
                live_idle.id: SimpleNamespace(
                    status=RecordingStatus.LIVE,
                    stream=SimpleNamespace(is_live=True),
                ),
                stopping.id: SimpleNamespace(
                    status=RecordingStatus.STOPPING,
                    stream=SimpleNamespace(is_live=True),
                ),
                failed.id: SimpleNamespace(
                    status=RecordingStatus.ERROR,
                    stream=SimpleNamespace(is_live=False),
                ),
                resolving.id: SimpleNamespace(
                    status=RecordingStatus.RESOLVING,
                    stream=None,
                ),
            }
        )

        self.assertEqual(self.root._target_states(unknown), ("Unknown", "Idle"))
        self.assertEqual(self.root._target_states(offline), ("Offline", "Idle"))
        self.assertEqual(self.root._target_states(live_idle), ("Live", "Idle"))
        self.assertEqual(self.root._target_states(stopping), ("Live", "Stopping"))
        self.assertEqual(self.root._target_states(failed), ("Offline", "Error"))
        self.assertEqual(
            self.root._target_states(resolving),
            ("Unknown", "Checking"),
        )
        self.assertEqual(self.root._target_states(self.paused), ("Unknown", "Paused"))

    def test_preview_switches_generation_and_marshals_updates_to_clock(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        self.root._target_rows["paused"].preview_button.trigger_action()

        self.assertEqual(self.preview.starts, [])
        self.assertEqual(
            self.root.preview_pane.states,
            [PreviewState.RESOLVING, PreviewState.RESOLVING],
        )
        self.preview_executor.run(0, threaded=True)
        self.preview_executor.run(1, threaded=True)
        FakeClock.flush()
        self.assertEqual([item[0].id for item in self.preview.starts], ["paused"])
        self.assertEqual(self.root._preview_generation, 1)
        self.assertEqual(
            self.root.preview_pane.metadata[-1],
            ("huya", "Room two", "Original"),
        )

        stale = PreviewUpdate(99, "enabled", PreviewState.RESOLVING)
        current = PreviewUpdate(1, "paused", PreviewState.RESOLVING)
        self.root._on_preview_update(1, stale)
        self.preview.emit(0, current)
        self.assertEqual(self.root.preview_pane.updates, [])
        self.assertEqual(len(FakeClock.once), 2)

        FakeClock.flush()
        self.assertEqual(self.root.preview_pane.updates, [current])

    def test_preview_start_and_stop_never_run_on_button_thread(self):
        button_thread = threading.get_ident()
        self.root._target_rows["enabled"].preview_button.trigger_action()

        self.assertEqual(self.preview.starts, [])
        self.assertEqual(self.root.preview_pane.states[-1], PreviewState.RESOLVING)
        self.preview_executor.run(0, threaded=True)
        FakeClock.flush()

        self.root.preview_pane.on_stop()
        self.assertEqual(self.preview.stop_calls, 0)
        self.assertEqual(self.root.preview_pane.states[-1], PreviewState.STOPPED)
        self.preview_executor.run(1, threaded=True)
        FakeClock.flush()

        self.assertNotEqual(self.preview.start_threads, [button_thread])
        self.assertNotEqual(self.preview.stop_threads, [button_thread])

    def test_start_callback_before_future_completion_keeps_first_state(self):
        self.preview.on_start = lambda generation, target: PreviewUpdate(
            generation,
            target.id,
            PreviewState.RESOLVING,
        )
        self.root._target_rows["enabled"].preview_button.trigger_action()

        self.preview_executor.run(0, threaded=True)
        self.assertEqual(self.root.preview_pane.updates, [])
        FakeClock.flush()

        self.assertEqual(len(self.root.preview_pane.updates), 1)
        self.assertIs(
            self.root.preview_pane.updates[0].state,
            PreviewState.RESOLVING,
        )
        self.assertEqual(self.root._preview_generation, 1)

    def test_preview_start_future_error_returns_to_clock_with_generic_text(self):
        self.preview.start_error = RuntimeError(
            "https://pull.example/live?token=secret Cookie: private-cookie"
        )
        self.root._target_rows["enabled"].preview_button.trigger_action()

        self.preview_executor.run(0, threaded=True)
        self.assertEqual(
            self.root.preview_pane.states,
            [PreviewState.RESOLVING],
        )
        self.assertNotIn("failed", self.root.status.text.lower())

        FakeClock.flush()

        self.assertEqual(self.root.preview_pane.states[-1], PreviewState.FAILED)
        combined = f"{self.root.status.text}\n{self.root.log.text}".lower()
        self.assertIn("failed", combined)
        self.assertNotIn("pull.example", combined)
        self.assertNotIn("secret", combined)
        self.assertNotIn("private-cookie", combined)

    def test_preview_submit_failure_invalidates_request_and_old_callback(self):
        class ClosedExecutor:
            def submit(self, _fn):
                raise RuntimeError(
                    "https://pull.example/live?token=secret Cookie: private-cookie"
                )

        previous = Future()
        self.root._preview_action_future = previous
        self.root._preview_log_generation = 7
        self.root._preview_log_state = PreviewState.PLAYING
        self.root.preview_executor = ClosedExecutor()
        failed_request_id = self.root._preview_request_id + 1

        self.root._target_rows["enabled"].preview_button.trigger_action()

        self.assertTrue(previous.cancelled())
        self.assertEqual(self.root._preview_request_id, failed_request_id + 1)
        self.assertIsNone(self.root._preview_generation)
        self.assertIsNone(self.root._preview_target_id)
        self.assertIsNone(self.root._preview_action_future)
        self.assertIsNone(self.root._preview_log_generation)
        self.assertIsNone(self.root._preview_log_state)
        self.assertEqual(
            self.root.preview_pane.states,
            [PreviewState.RESOLVING, PreviewState.FAILED],
        )
        combined = f"{self.root.status.text}\n{self.root.log.text}".lower()
        self.assertIn("failed", combined)
        self.assertNotIn("pull.example", combined)
        self.assertNotIn("secret", combined)
        self.assertNotIn("private-cookie", combined)

        self.root._on_preview_update(
            failed_request_id,
            PreviewUpdate(
                99,
                "enabled",
                PreviewState.PLAYING,
            ),
        )
        FakeClock.flush()
        self.assertEqual(self.root.preview_pane.updates, [])

    def test_rapid_start_switch_stop_discards_all_old_callbacks(self):
        self.preview.on_start = lambda generation, target: PreviewUpdate(
            generation,
            target.id,
            PreviewState.PLAYING,
        )
        self.root._target_rows["enabled"].preview_button.trigger_action()
        self.root._target_rows["paused"].preview_button.trigger_action()
        self.root.preview_pane.on_stop()

        self.assertEqual(
            self.root.preview_pane.states,
            [
                PreviewState.RESOLVING,
                PreviewState.RESOLVING,
                PreviewState.STOPPED,
            ],
        )
        self.assertEqual(self.preview.starts, [])
        self.assertEqual(self.preview.stop_calls, 0)

        for index in range(3):
            self.preview_executor.run(index, threaded=True)
        FakeClock.flush()

        self.assertEqual(self.root.preview_pane.updates, [])
        self.assertEqual(self.preview.stop_calls, 1)
        self.assertIsNone(self.root._preview_generation)
        self.assertIsNone(self.root._preview_target_id)

    def test_preview_requests_cancel_pending_middle_action(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        running = self.preview_executor.mark_running(0)

        self.root._target_rows["paused"].preview_button.trigger_action()
        pending = self.preview_executor.submissions[1][1]
        self.assertFalse(pending.cancelled())

        self.root._target_rows["enabled"].preview_button.trigger_action()

        latest = self.preview_executor.submissions[2][1]
        self.assertTrue(running.running())
        self.assertTrue(pending.cancelled())
        self.assertFalse(latest.cancelled())
        self.assertIs(self.root._preview_action_future, latest)

    def test_stop_cancels_pending_start_behind_one_running_action(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        running = self.preview_executor.mark_running(0)
        self.root._target_rows["paused"].preview_button.trigger_action()
        pending_start = self.preview_executor.submissions[1][1]

        self.root.preview_pane.on_stop()

        latest_stop = self.preview_executor.submissions[2][1]
        self.assertTrue(running.running())
        self.assertTrue(pending_start.cancelled())
        self.assertFalse(latest_stop.cancelled())
        self.assertIs(self.root._preview_action_future, latest_stop)

    def test_shutdown_skips_many_cancelled_preview_starts(self):
        for target_id in ("enabled", "paused", "enabled", "paused", "enabled"):
            self.root._target_rows[target_id].preview_button.trigger_action()

        start_futures = [
            future for _fn, future in self.preview_executor.submissions
        ]
        app = SimpleNamespace(
            desktop_root=self.root,
            preview_session=self.preview,
            preview_executor=self.preview_executor,
            target_action_executor=self.target_action_executor,
            scheduler=SimpleNamespace(shutdown=lambda: None),
            executor=self.executor,
        )

        desktop_main.LuboDesktopApp.on_stop(app)

        self.assertTrue(all(future.cancelled() for future in start_futures))
        self.assertEqual(self.preview.starts, [])
        self.assertEqual(self.preview.stop_calls, 1)
        self.assertEqual(self.preview.close_calls, 1)
        self.assertTrue(self.preview_executor.submissions[-2][1].cancelled())
        self.assertEqual(
            self.preview_executor.shutdown_calls,
            [{"wait": True, "cancel_futures": False}],
        )

    def test_preview_state_logs_are_structured_sanitized_and_deduplicated(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        self.preview_executor.run(0, threaded=True)
        FakeClock.flush()
        updates = (
            PreviewUpdate(1, "enabled", PreviewState.RESOLVING),
            PreviewUpdate(1, "enabled", PreviewState.PLAYING),
            PreviewUpdate(1, "enabled", PreviewState.PLAYING),
            PreviewUpdate(
                1,
                "enabled",
                PreviewState.FAILED,
                message=(
                    "https://pull.example/live?token=stream-secret "
                    "Cookie: sessionid=cookie-secret"
                ),
            ),
        )

        with self.assertLogs("lubo.apps.desktop.main", level="INFO") as captured:
            for update in updates:
                self.root._on_preview_update(
                    self.root._preview_request_id,
                    update,
                )
            FakeClock.flush()

        output = "\n".join(captured.output)
        self.assertEqual(output.count("event=preview_state"), 3)
        self.assertEqual(output.count("state=playing"), 1)
        self.assertIn("target=enabled", output)
        self.assertIn("state=resolving", output)
        self.assertIn("ERROR:lubo.apps.desktop.main", output)
        self.assertIn("state=failed", output)
        self.assertIn("message=<redacted-url> Cookie: <redacted>", output)
        self.assertNotIn("pull.example", output)
        self.assertNotIn("stream-secret", output)
        self.assertNotIn("cookie-secret", output)

    def test_stop_and_switch_reset_preview_state_log_deduplication(self):
        row = self.root._target_rows["enabled"]
        row.preview_button.trigger_action()
        self.preview_executor.run(0, threaded=True)
        FakeClock.flush()

        with self.assertLogs("lubo.apps.desktop.main", level="INFO") as captured:
            self.preview.emit(
                0,
                PreviewUpdate(1, "enabled", PreviewState.PLAYING),
            )
            FakeClock.flush()
            self.root.preview_pane.on_stop()
            self.preview_executor.run(1, threaded=True)
            FakeClock.flush()
            row.preview_button.trigger_action()
            self.preview_executor.run(2, threaded=True)
            FakeClock.flush()
            self.preview.emit(
                1,
                PreviewUpdate(2, "enabled", PreviewState.PLAYING),
            )
            FakeClock.flush()

        output = "\n".join(captured.output)
        self.assertEqual(output.count("state=playing"), 2)
        self.assertEqual(output.count("state=stopped"), 1)

    def test_preview_stop_button_only_stops_preview(self):
        self.root.preview_pane.on_stop()

        self.assertEqual(self.preview.stop_calls, 0)
        self.assertEqual(len(self.preview_executor.submissions), 1)
        self.assertEqual(self.root.preview_pane.states[-1], PreviewState.STOPPED)
        self.assertEqual(self.executor.submissions, [])
        self.assertEqual(self.target_action_executor.submissions, [])
        self.assertEqual(self.controller.pause_calls, [])

    def test_stop_preview_invalidates_queued_update_and_clears_pane_immediately(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        self.preview_executor.run(0, threaded=True)
        FakeClock.flush()
        playing = PreviewUpdate(
            generation=1,
            target_id="enabled",
            state=PreviewState.PLAYING,
        )
        self.preview.emit(0, playing)
        self.assertEqual(self.root.preview_pane.updates, [])

        self.root.preview_pane.on_stop()

        self.assertEqual(self.preview.stop_calls, 0)
        self.assertIsNone(self.root._preview_generation)
        self.assertIsNone(self.root._preview_target_id)
        self.assertEqual(self.root.preview_pane.states[-1], PreviewState.STOPPED)
        self.assertEqual(self.root.preview_pane.updates, [])

        self.preview_executor.run(1, threaded=True)
        FakeClock.flush()
        self.assertEqual(self.preview.stop_calls, 1)
        self.assertEqual(self.root.preview_pane.updates, [])

    def test_stop_and_pause_stops_active_preview_then_runs_on_queue(self):
        row = self.root._target_rows["enabled"]
        row.preview_button.trigger_action()
        row.pause_button.trigger_action()
        row.pause_button.trigger_action()

        self.assertEqual(self.preview.stop_calls, 0)
        self.assertEqual(len(self.preview_executor.submissions), 2)
        self.assertEqual(self.executor.submissions, [])
        self.assertEqual(len(self.target_action_executor.submissions), 1)
        self.assertTrue(row.preview_button.disabled)
        self.assertTrue(row.pause_button.disabled)
        self.assertTrue(row.resume_button.disabled)
        fn, future = self.target_action_executor.submissions[0]
        fn()
        future.set_result(None)
        self.assertEqual(self.controller.pause_calls, ["enabled"])
        self.assertEqual(self.preview.stop_calls, 0)
        self.assertGreaterEqual(len(FakeClock.once), 1)

        FakeClock.flush()
        self.assertIn("paused", self.root.status.text.lower())
        self.assertFalse(self.root._target_rows["enabled"].enabled)

    def test_resume_runs_on_queue_and_refreshes_on_main_thread(self):
        row = self.root._target_rows["paused"]
        row.resume_button.trigger_action()

        self.assertEqual(self.executor.submissions, [])
        self.assertEqual(len(self.target_action_executor.submissions), 1)
        fn, future = self.target_action_executor.submissions[0]
        fn()
        future.set_result(None)
        self.assertEqual(self.controller.resume_calls, ["paused"])
        self.assertFalse(self.root._target_rows["paused"].enabled)

        FakeClock.flush()
        self.assertTrue(self.root._target_rows["paused"].enabled)
        self.assertIn("resumed", self.root.status.text.lower())

    def test_target_action_error_is_generic_sanitized_and_reenables_row(self):
        row = self.root._target_rows["enabled"]
        row.pause_button.trigger_action()
        _, future = self.target_action_executor.submissions[0]
        future.set_exception(
            RuntimeError("https://pull.example/live?token=secret cookie=session-secret")
        )

        self.assertEqual(self.root.log.text, "")
        FakeClock.flush()

        combined = f"{self.root.status.text}\n{self.root.log.text}".lower()
        self.assertIn("failed", combined)
        self.assertNotIn("pull.example", combined)
        self.assertNotIn("secret", combined)
        self.assertFalse(self.root._target_rows["enabled"].pause_button.disabled)

    def test_background_event_log_redacts_rtmp_stream_and_cookie(self):
        event = RecorderEvent(
            type=RecorderEventType.ERROR,
            target_id="enabled",
            message=(
                "rtmps://pull.example/live?token=stream-secret "
                "Cookie: sessionid=cookie-secret"
            ),
        )

        self.root._on_event(event)
        self.assertEqual(self.root.log.text, "")
        FakeClock.flush()

        self.assertIn("<redacted-url>", self.root.log.text)
        self.assertIn("Cookie: <redacted>", self.root.log.text)
        self.assertNotIn("pull.example", self.root.log.text)
        self.assertNotIn("stream-secret", self.root.log.text)
        self.assertNotIn("cookie-secret", self.root.log.text)

    def test_close_stops_preview_unsubscribes_watch_and_blocks_late_ui(self):
        self.root._toggle_watch(None)
        self.assertEqual(len(FakeClock.intervals), 1)
        FakeClock.once.clear()
        self.root._target_rows["enabled"].pause_button.trigger_action()
        _, future = self.target_action_executor.submissions[-1]

        self.root.close()
        future.set_result(None)
        update = SimpleNamespace(generation=1, target_id="enabled")
        self.root._on_preview_update(self.root._preview_request_id, update)

        self.assertTrue(FakeClock.intervals[0].cancelled)
        self.assertEqual(self.preview.stop_calls, 0)
        self.assertEqual(len(self.preview_executor.submissions), 1)
        self.assertEqual(self.events.unsubscribers, [self.root._on_event])
        self.assertEqual(FakeClock.once, [])

    def test_pause_uses_action_queue_while_check_is_still_blocked(self):
        self.root._submit_check()
        self.assertEqual(len(self.executor.submissions), 1)
        _, check_future = self.executor.submissions[0]
        self.assertFalse(check_future.done())

        row = self.root._target_rows["enabled"]
        row.pause_button.trigger_action()
        row.pause_button.trigger_action()

        self.assertEqual(len(self.executor.submissions), 1)
        self.assertEqual(len(self.target_action_executor.submissions), 1)
        action, action_future = self.target_action_executor.submissions[0]
        action()
        action_future.set_result(None)

        self.assertFalse(check_future.done())
        self.assertEqual(self.controller.pause_calls, ["enabled"])
        self.assertFalse(self.controller.target_by_id("enabled").enabled)
        FakeClock.flush()
        self.assertFalse(self.root._target_rows["enabled"].enabled)

    def test_close_invalidates_active_preview_without_touching_pane(self):
        self.root._target_rows["enabled"].preview_button.trigger_action()
        self.preview_executor.run(0, threaded=True)
        FakeClock.flush()
        playing = PreviewUpdate(
            generation=1,
            target_id="enabled",
            state=PreviewState.PLAYING,
        )
        self.preview.emit(0, playing)

        self.root.close()

        self.assertEqual(self.preview.stop_calls, 0)
        self.preview_executor.run(1, threaded=True)
        self.assertEqual(self.preview.stop_calls, 1)
        self.assertIsNone(self.root._preview_generation)
        self.assertIsNone(self.root._preview_target_id)
        self.assertEqual(self.root.preview_pane.updates, [])
        FakeClock.flush()
        self.assertEqual(self.root.preview_pane.updates, [])

    def test_on_stop_drains_preview_and_actions_before_scheduler_once(self):
        calls = []

        def submit_preview(fn):
            calls.append("preview_executor.submit")
            future = Future()
            future.set_result(fn())
            return future

        app = SimpleNamespace(
            desktop_root=SimpleNamespace(close=lambda: calls.append("root.close")),
            preview_session=SimpleNamespace(close=lambda: calls.append("preview.close")),
            scheduler=SimpleNamespace(shutdown=lambda: calls.append("scheduler.shutdown")),
            executor=SimpleNamespace(
                shutdown=lambda **kwargs: calls.append(("executor.shutdown", kwargs))
            ),
            target_action_executor=SimpleNamespace(
                shutdown=lambda **kwargs: calls.append(
                    ("target_action_executor.shutdown", kwargs)
                )
            ),
            preview_executor=SimpleNamespace(
                submit=submit_preview,
                shutdown=lambda **kwargs: calls.append(
                    ("preview_executor.shutdown", kwargs)
                ),
            ),
        )

        desktop_main.LuboDesktopApp.on_stop(app)
        desktop_main.LuboDesktopApp.on_stop(app)

        self.assertEqual(
            calls,
            [
                "root.close",
                "preview_executor.submit",
                "preview.close",
                (
                    "preview_executor.shutdown",
                    {"wait": True, "cancel_futures": False},
                ),
                (
                    "target_action_executor.shutdown",
                    {"wait": True, "cancel_futures": True},
                ),
                "scheduler.shutdown",
                ("executor.shutdown", {"wait": False, "cancel_futures": True}),
            ],
        )

    def test_on_stop_waits_for_running_action_and_cancels_queued_action(self):
        order = []
        action_started = threading.Event()
        release_action = threading.Event()
        scheduler_closed = threading.Event()
        target_queue = desktop_main.DaemonTaskQueue(
            thread_name="test-target-actions"
        )

        def running_action():
            order.append("action-start")
            action_started.set()
            release_action.wait(2)
            order.append(
                "late-action-write"
                if scheduler_closed.is_set()
                else "action-write"
            )

        target_queue.submit(running_action)
        queued = target_queue.submit(lambda: order.append("queued-action-write"))
        self.assertTrue(action_started.wait(1))

        def submit_preview(fn):
            future = Future()
            future.set_result(fn())
            return future

        app = SimpleNamespace(
            desktop_root=SimpleNamespace(close=lambda: None),
            preview_session=SimpleNamespace(close=lambda: None),
            preview_executor=SimpleNamespace(
                submit=submit_preview,
                shutdown=lambda **_kwargs: None,
            ),
            target_action_executor=target_queue,
            scheduler=SimpleNamespace(
                shutdown=lambda: (
                    scheduler_closed.set(),
                    order.append("scheduler-shutdown"),
                )
            ),
            executor=SimpleNamespace(shutdown=lambda **_kwargs: None),
        )
        stop_thread = threading.Thread(
            target=desktop_main.LuboDesktopApp.on_stop,
            args=(app,),
        )
        stop_thread.start()
        try:
            self.assertFalse(scheduler_closed.wait(0.1))
            release_action.set()
            stop_thread.join(2)
            self.assertFalse(stop_thread.is_alive())
        finally:
            release_action.set()
            stop_thread.join(2)

        self.assertTrue(queued.cancelled())
        self.assertEqual(
            order,
            ["action-start", "action-write", "scheduler-shutdown"],
        )


if __name__ == "__main__":
    unittest.main()
