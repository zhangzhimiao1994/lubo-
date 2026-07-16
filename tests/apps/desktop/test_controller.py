import asyncio
import importlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from lubo.apps.desktop.controller import DesktopController
from lubo.core.config import AppConfig
from lubo.core.models import Quality, RecordingTarget
from lubo.core.url_store import UrlStore


class FakeConfigService:
    def __init__(self, config):
        self.config = config
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return self.config


class FakeScheduler:
    def __init__(self):
        self.checked_targets = None
        self.stop_all_calls = 0
        self.pause_target_calls = []
        self.resume_target_calls = []

    async def check_once(self, targets):
        self.checked_targets = targets

    def stop_all(self):
        self.stop_all_calls += 1

    def pause_target(self, target_id):
        self.pause_target_calls.append(target_id)

    def resume_target(self, target_id):
        self.resume_target_calls.append(target_id)


class FakeUrlStore:
    def __init__(self, save_error=None):
        self.save_error = save_error
        self.save_calls = 0
        self.saved_targets = None

    def add(self, targets, url, quality=Quality.ORIGINAL, name=""):
        return [
            *targets,
            RecordingTarget(url=url, quality=quality, display_name=name),
        ]

    def save(self, targets):
        self.save_calls += 1
        self.saved_targets = targets
        if self.save_error:
            raise self.save_error


class DesktopControllerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def import_desktop_main():
        class KivyWidget:
            pass

        modules = {
            "kivy": ModuleType("kivy"),
            "kivy.app": ModuleType("kivy.app"),
            "kivy.clock": ModuleType("kivy.clock"),
            "kivy.core": ModuleType("kivy.core"),
            "kivy.core.text": ModuleType("kivy.core.text"),
            "kivy.graphics": ModuleType("kivy.graphics"),
            "kivy.graphics.texture": ModuleType("kivy.graphics.texture"),
            "kivy.uix": ModuleType("kivy.uix"),
            "kivy.uix.boxlayout": ModuleType("kivy.uix.boxlayout"),
            "kivy.uix.button": ModuleType("kivy.uix.button"),
            "kivy.uix.floatlayout": ModuleType("kivy.uix.floatlayout"),
            "kivy.uix.image": ModuleType("kivy.uix.image"),
            "kivy.uix.label": ModuleType("kivy.uix.label"),
            "kivy.uix.scrollview": ModuleType("kivy.uix.scrollview"),
            "kivy.uix.textinput": ModuleType("kivy.uix.textinput"),
        }
        modules["kivy.app"].App = KivyWidget
        modules["kivy.clock"].Clock = object()
        modules["kivy.core.text"].LabelBase = object()
        modules["kivy.graphics.texture"].Texture = object()
        modules["kivy.uix.boxlayout"].BoxLayout = KivyWidget
        modules["kivy.uix.button"].Button = KivyWidget
        modules["kivy.uix.floatlayout"].FloatLayout = KivyWidget
        modules["kivy.uix.image"].Image = KivyWidget
        modules["kivy.uix.label"].Label = KivyWidget
        modules["kivy.uix.scrollview"].ScrollView = KivyWidget
        modules["kivy.uix.textinput"].TextInput = KivyWidget
        desktop_module_name = "lubo.apps.desktop.main"
        desktop_package = importlib.import_module("lubo.apps.desktop")
        tracked_module_names = (desktop_module_name, *modules)
        saved_modules = {
            name: sys.modules[name]
            for name in tracked_module_names
            if name in sys.modules
        }
        saved_package_main = desktop_package.__dict__.get("main")
        had_package_main = "main" in desktop_package.__dict__

        try:
            for name in tracked_module_names:
                sys.modules.pop(name, None)
            sys.modules.update(modules)
            desktop_package.__dict__.pop("main", None)
            return importlib.import_module(desktop_module_name)
        finally:
            for name in tracked_module_names:
                sys.modules.pop(name, None)
            sys.modules.update(saved_modules)
            if had_package_main:
                desktop_package.main = saved_package_main
            else:
                desktop_package.__dict__.pop("main", None)

    def make_controller(self, url_file):
        config = AppConfig()
        config_service = FakeConfigService(config)
        scheduler = FakeScheduler()
        controller = DesktopController(config_service, url_file, scheduler)
        return controller, config, config_service, scheduler

    async def test_add_target_persists_normalized_target_and_checks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            controller, _, _, scheduler = self.make_controller(url_file)

            controller.add_target(
                "live.douyin.com/123",
                quality=Quality.HIGH,
                name="anchor-a",
            )
            await controller.check_once()

            self.assertEqual(len(controller.targets), 1)
            target = controller.targets[0]
            self.assertEqual(target.url, "https://live.douyin.com/123")
            self.assertEqual(target.quality, Quality.HIGH)
            self.assertEqual(target.display_name, "anchor-a")
            self.assertIsNot(scheduler.checked_targets, controller.targets)
            self.assertEqual(scheduler.checked_targets, controller.targets)

            reloaded = UrlStore(url_file).load()
            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].url, "https://live.douyin.com/123")
            self.assertEqual(reloaded[0].quality, Quality.HIGH)
            self.assertEqual(reloaded[0].display_name, "anchor-a")

    def test_concurrent_set_enabled_and_add_preserve_both_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            original = RecordingTarget(
                "https://live.douyin.com/original",
                id="original",
            )
            controller.targets = [original]
            barrier = threading.Barrier(2)
            release_first_save = threading.Event()
            add_attempted = threading.Event()

            class BlockingStore(FakeUrlStore):
                def __init__(self):
                    super().__init__()
                    self.snapshots = []
                    self.calls_lock = threading.Lock()

                def save(self, targets):
                    with self.calls_lock:
                        self.save_calls += 1
                        call_number = self.save_calls
                        self.snapshots.append(list(targets))
                    if call_number == 1:
                        barrier.wait(timeout=1)
                        release_first_save.wait(2)

            store = BlockingStore()
            controller.url_store = store

            set_thread = threading.Thread(
                target=controller.set_target_enabled,
                args=(original.id, False),
            )

            def add_target():
                barrier.wait(timeout=1)
                add_attempted.set()
                controller.add_target("https://live.douyin.com/new")

            add_thread = threading.Thread(target=add_target)
            add_thread.start()
            set_thread.start()
            try:
                self.assertTrue(add_attempted.wait(1))
                self.assertTrue(add_thread.is_alive())
                self.assertEqual(store.save_calls, 1)
            finally:
                release_first_save.set()
                set_thread.join(2)
                add_thread.join(2)

            self.assertFalse(set_thread.is_alive())
            self.assertFalse(add_thread.is_alive())
            self.assertEqual(len(controller.targets), 2)
            self.assertFalse(controller.target_by_id(original.id).enabled)
            self.assertTrue(
                any(target.url.endswith("/new") for target in controller.targets)
            )
            self.assertFalse(store.snapshots[-1][0].enabled)
            self.assertEqual(len(store.snapshots[-1]), 2)

    def test_pause_releases_config_lock_before_blocking_scheduler_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            original = RecordingTarget(
                "https://live.douyin.com/original",
                id="original",
            )
            controller.targets = [original]
            controller.url_store = FakeUrlStore()
            pause_started = threading.Event()
            release_pause = threading.Event()

            def blocking_pause(target_id):
                pause_started.set()
                release_pause.wait(2)
                scheduler.pause_target_calls.append(target_id)

            scheduler.pause_target = blocking_pause
            pause_thread = threading.Thread(
                target=controller.stop_and_pause_target,
                args=(original.id,),
            )
            pause_thread.start()
            self.assertTrue(pause_started.wait(1))

            add_thread = threading.Thread(
                target=controller.add_target,
                args=("https://live.douyin.com/new",),
            )
            add_thread.start()
            add_thread.join(1)
            try:
                self.assertFalse(add_thread.is_alive())
                self.assertEqual(len(controller.targets), 2)
            finally:
                release_pause.set()
                pause_thread.join(2)
                add_thread.join(2)

    async def test_check_once_uses_snapshot_and_releases_lock_before_await(self):
        class BlockingScheduler:
            def __init__(self):
                self.checked_targets = None
                self.started = threading.Event()
                self.release = threading.Event()

            async def check_once(self, targets):
                self.checked_targets = targets
                self.started.set()
                await asyncio.to_thread(self.release.wait, 2)

        with tempfile.TemporaryDirectory() as tmp:
            scheduler = BlockingScheduler()
            controller = DesktopController(
                FakeConfigService(AppConfig()),
                Path(tmp) / "URL_config.ini",
                scheduler,
            )
            original = RecordingTarget(
                "https://live.douyin.com/original",
                id="original",
            )
            controller.targets = [original]
            controller.url_store = FakeUrlStore()
            original_targets = controller.targets

            check_task = asyncio.create_task(controller.check_once())
            self.assertTrue(await asyncio.to_thread(scheduler.started.wait, 1))
            self.assertIsNot(scheduler.checked_targets, original_targets)

            add_done = threading.Event()

            def add_target():
                controller.add_target("https://live.douyin.com/new")
                add_done.set()

            add_thread = threading.Thread(target=add_target)
            add_thread.start()
            try:
                self.assertTrue(await asyncio.to_thread(add_done.wait, 1))
            finally:
                scheduler.release.set()
                await check_task
                add_thread.join(2)

            self.assertEqual(
                [target.id for target in scheduler.checked_targets],
                ["original"],
            )
            self.assertEqual(len(controller.targets), 2)

    def test_stop_all_delegates_to_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )

            controller.stop_all()

            self.assertEqual(scheduler.stop_all_calls, 1)

    def test_remove_target_persists_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            UrlStore(url_file).save(
                [
                    RecordingTarget("https://live.douyin.com/111"),
                    RecordingTarget("https://live.douyin.com/222"),
                ]
            )
            controller, _, _, _ = self.make_controller(url_file)

            controller.remove_target(controller.targets[0].id)

            reloaded = UrlStore(url_file).load()
            self.assertEqual(
                [target.url for target in reloaded],
                ["https://live.douyin.com/222"],
            )

    def test_set_target_enabled_persists_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            UrlStore(url_file).save(
                [RecordingTarget("https://live.douyin.com/333")]
            )
            controller, _, _, _ = self.make_controller(url_file)
            target_id = controller.targets[0].id

            controller.set_target_enabled(target_id, False)

            reloaded = UrlStore(url_file).load()
            self.assertEqual(len(reloaded), 1)
            self.assertFalse(reloaded[0].enabled)

    def test_target_by_id_returns_target_or_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget("https://live.douyin.com/333")
            controller.targets = [target]

            self.assertIs(controller.target_by_id(target.id), target)
            self.assertIsNone(controller.target_by_id("missing"))

    def test_stop_and_pause_persists_disabled_before_scheduler_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget("https://live.douyin.com/333")
            controller.targets = [target]
            store = FakeUrlStore()
            controller.url_store = store

            def assert_persisted_before_pause(target_id):
                self.assertEqual(target_id, target.id)
                self.assertEqual(store.save_calls, 1)
                self.assertFalse(controller.target_by_id(target.id).enabled)
                scheduler.pause_target_calls.append(target_id)

            scheduler.pause_target = assert_persisted_before_pause

            controller.stop_and_pause_target(target.id)

            self.assertEqual(scheduler.pause_target_calls, [target.id])
            self.assertTrue(target.enabled)

    def test_resume_persists_enabled_before_scheduler_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget(
                "https://live.douyin.com/333",
                enabled=False,
            )
            controller.targets = [target]
            store = FakeUrlStore()
            controller.url_store = store

            def assert_persisted_before_resume(target_id):
                self.assertEqual(target_id, target.id)
                self.assertEqual(store.save_calls, 1)
                self.assertTrue(controller.target_by_id(target.id).enabled)
                scheduler.resume_target_calls.append(target_id)

            scheduler.resume_target = assert_persisted_before_resume

            controller.resume_target(target.id)

            self.assertEqual(scheduler.resume_target_calls, [target.id])
            self.assertFalse(target.enabled)

    def test_unknown_pause_and_resume_are_no_ops(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            store = FakeUrlStore()
            controller.url_store = store

            controller.stop_and_pause_target("missing")
            controller.resume_target("missing")

            self.assertEqual(store.save_calls, 0)
            self.assertEqual(scheduler.pause_target_calls, [])
            self.assertEqual(scheduler.resume_target_calls, [])

    def test_pause_save_failure_does_not_call_scheduler_or_mutate_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget("https://live.douyin.com/333")
            controller.targets = [target]
            controller.url_store = FakeUrlStore(RuntimeError("save failed"))

            with self.assertRaisesRegex(RuntimeError, "save failed"):
                controller.stop_and_pause_target(target.id)

            self.assertIs(controller.targets[0], target)
            self.assertTrue(target.enabled)
            self.assertEqual(scheduler.pause_target_calls, [])

    def test_resume_save_failure_does_not_call_scheduler_or_mutate_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, scheduler = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget(
                "https://live.douyin.com/333",
                enabled=False,
            )
            controller.targets = [target]
            controller.url_store = FakeUrlStore(RuntimeError("save failed"))

            with self.assertRaisesRegex(RuntimeError, "save failed"):
                controller.resume_target(target.id)

            self.assertIs(controller.targets[0], target)
            self.assertFalse(target.enabled)
            self.assertEqual(scheduler.resume_target_calls, [])

    def test_add_target_save_failure_keeps_memory_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            original = RecordingTarget("https://live.douyin.com/111")
            controller.targets = [original]
            original_targets = controller.targets
            controller.url_store = FakeUrlStore(RuntimeError("save failed"))

            with self.assertRaisesRegex(RuntimeError, "save failed"):
                controller.add_target("live.douyin.com/222")

            self.assertIs(controller.targets, original_targets)
            self.assertEqual(controller.targets, [original])
            self.assertIs(controller.targets[0], original)

    def test_remove_target_save_failure_keeps_memory_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            first = RecordingTarget("https://live.douyin.com/111")
            second = RecordingTarget("https://live.douyin.com/222")
            controller.targets = [first, second]
            original_targets = controller.targets
            controller.url_store = FakeUrlStore(RuntimeError("save failed"))

            with self.assertRaisesRegex(RuntimeError, "save failed"):
                controller.remove_target(first.id)

            self.assertIs(controller.targets, original_targets)
            self.assertEqual(controller.targets, [first, second])
            self.assertIs(controller.targets[0], first)
            self.assertIs(controller.targets[1], second)

    def test_set_enabled_save_failure_does_not_mutate_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget("https://live.douyin.com/333")
            controller.targets = [target]
            original_targets = controller.targets
            store = FakeUrlStore(RuntimeError("save failed"))
            controller.url_store = store

            with self.assertRaisesRegex(RuntimeError, "save failed"):
                controller.set_target_enabled(target.id, False)

            self.assertIs(controller.targets, original_targets)
            self.assertIs(controller.targets[0], target)
            self.assertTrue(target.enabled)
            self.assertIsNot(store.saved_targets[0], target)
            self.assertFalse(store.saved_targets[0].enabled)

    def test_unknown_target_ids_do_not_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller, _, _, _ = self.make_controller(
                Path(tmp) / "URL_config.ini"
            )
            target = RecordingTarget("https://live.douyin.com/444")
            controller.targets = [target]
            store = FakeUrlStore()
            controller.url_store = store

            controller.remove_target("missing")
            controller.set_target_enabled("missing", False)

            self.assertEqual(store.save_calls, 0)
            self.assertEqual(controller.targets, [target])
            self.assertIs(controller.targets[0], target)

    def test_initialization_loads_config_and_existing_url_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            UrlStore(url_file).save(
                [
                    RecordingTarget(
                        "https://live.douyin.com/444",
                        quality=Quality.ULTRA,
                        display_name="anchor-b",
                    )
                ]
            )

            controller, config, config_service, _ = self.make_controller(url_file)

            self.assertIs(controller.config, config)
            self.assertEqual(config_service.load_calls, 1)
            self.assertEqual(len(controller.targets), 1)
            self.assertEqual(
                controller.targets[0].url,
                "https://live.douyin.com/444",
            )
            self.assertEqual(controller.targets[0].quality, Quality.ULTRA)
            self.assertEqual(controller.targets[0].display_name, "anchor-b")

    def test_plain_urls_and_new_targets_inherit_global_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            url_file.write_text(
                "https://live.douyin.com/111\n",
                encoding="utf-8-sig",
            )
            config = AppConfig(quality=Quality.HIGH)
            controller = DesktopController(
                FakeConfigService(config),
                url_file,
                FakeScheduler(),
            )

            controller.add_target("https://live.douyin.com/222")

            self.assertEqual(
                [target.quality for target in controller.targets],
                [Quality.HIGH, Quality.HIGH],
            )

    def test_desktop_app_build_uses_default_registry_and_all_platform_cookies(self):
        imported_module_names = (
            "lubo.apps.desktop.main",
            "kivy",
            "kivy.app",
            "kivy.clock",
            "kivy.core",
            "kivy.core.text",
            "kivy.graphics",
            "kivy.graphics.texture",
            "kivy.uix",
            "kivy.uix.boxlayout",
            "kivy.uix.button",
            "kivy.uix.floatlayout",
            "kivy.uix.image",
            "kivy.uix.label",
            "kivy.uix.scrollview",
            "kivy.uix.textinput",
        )
        saved_modules = {
            name: sys.modules[name]
            for name in imported_module_names
            if name in sys.modules
        }
        desktop_package = importlib.import_module("lubo.apps.desktop")
        saved_package_main = desktop_package.__dict__.get("main")
        had_package_main = "main" in desktop_package.__dict__
        for name in imported_module_names:
            sys.modules.pop(name, None)
        desktop_package.__dict__.pop("main", None)

        def restore_modules():
            for name in imported_module_names:
                sys.modules.pop(name, None)
            sys.modules.update(saved_modules)
            if had_package_main:
                desktop_package.main = saved_package_main
            else:
                desktop_package.__dict__.pop("main", None)

        self.addCleanup(restore_modules)
        desktop_main = self.import_desktop_main()

        for name in imported_module_names:
            self.assertNotIn(name, sys.modules)
        self.assertNotIn("main", desktop_package.__dict__)

        cookies = {
            "douyin": "douyin-cookie",
            "bilibili": "bilibili-cookie",
            "huya": "huya-cookie",
            "douyu": "douyu-cookie",
        }
        config = AppConfig(cookies=cookies)
        registry = object()
        preview_resolver = object()
        scheduler = SimpleNamespace(resolve_preview_stream=preview_resolver)
        desktop_root = object()
        preview_session = object()
        check_executor = object()
        target_action_executor = object()
        preview_executor = object()
        config_path = Path("config.ini")
        url_path = Path("URL_config.ini")
        output_dir = Path("recordings")
        app = SimpleNamespace(user_data_dir="desktop-data")
        event_bus = Mock()

        with (
            patch.object(
                desktop_main,
                "_prepare_user_config",
                return_value=(config_path, url_path),
            ),
            patch.object(
                desktop_main,
                "_prepare_output_dir",
                return_value=output_dir,
            ),
            patch.object(desktop_main, "ConfigService") as config_service_type,
            patch.object(desktop_main, "EventBus", return_value=event_bus),
            patch.object(
                desktop_main,
                "build_default_registry",
                return_value=registry,
            ) as build_registry,
            patch.object(desktop_main, "resolve_ffmpeg", return_value="ffmpeg"),
            patch.object(desktop_main, "FFmpegRecorder", return_value=object()),
            patch.object(
                desktop_main,
                "RecordingScheduler",
                return_value=scheduler,
            ) as scheduler_type,
            patch.object(desktop_main, "DesktopController", return_value=object()),
            patch.object(
                desktop_main,
                "DaemonTaskQueue",
                side_effect=(
                    check_executor,
                    target_action_executor,
                    preview_executor,
                ),
            ) as task_queue_type,
            patch.object(
                desktop_main,
                "PreviewSession",
                return_value=preview_session,
            ) as preview_session_type,
            patch.object(desktop_main, "_register_cjk_font", return_value=None),
            patch.object(
                desktop_main,
                "DesktopRoot",
                return_value=desktop_root,
            ) as desktop_root_type,
        ):
            config_service_type.return_value.load.return_value = config

            result = desktop_main.LuboDesktopApp.build(app)

        self.assertIs(result, desktop_root)
        build_registry.assert_called_once_with()
        scheduler_config = scheduler_type.call_args.kwargs["config"]
        self.assertIs(scheduler_type.call_args.kwargs["registry"], registry)
        self.assertEqual(scheduler_config.cookies, cookies)
        self.assertIsInstance(scheduler_config.cookies, dict)
        self.assertIsNot(scheduler_config.cookies, config.cookies)
        preview_session_type.assert_called_once_with(
            resolver=preview_resolver,
            decoder_factory=desktop_main.PyAvDecoder,
        )
        self.assertIs(
            desktop_root_type.call_args.kwargs["preview_session"],
            preview_session,
        )
        self.assertEqual(
            [call.kwargs for call in task_queue_type.call_args_list],
            [
                {"thread_name": "recorder-check"},
                {"thread_name": "target-actions"},
                {"thread_name": "preview-actions"},
            ],
        )
        self.assertIs(desktop_root_type.call_args.args[2], check_executor)
        self.assertIs(
            desktop_root_type.call_args.kwargs["target_action_executor"],
            target_action_executor,
        )
        self.assertIsNot(check_executor, target_action_executor)
        self.assertIs(
            desktop_root_type.call_args.kwargs["preview_executor"],
            preview_executor,
        )
        self.assertIsNot(preview_executor, check_executor)
        self.assertIsNot(preview_executor, target_action_executor)
        event_bus.subscribe.assert_called_once_with(desktop_main._log_recorder_event)

    def test_desktop_url_prompt_is_platform_neutral(self):
        source = (
            Path(__file__).resolve().parents[3] / "lubo" / "apps" / "desktop" / "main.py"
        ).read_text(encoding="utf-8")

        self.assertIn('self._text("直播间 URL", "Live room URL")', source)
        self.assertNotIn("抖音直播间 URL", source)
        self.assertNotIn("Douyin live room URL", source)


if __name__ == "__main__":
    unittest.main()
