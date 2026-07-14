import tempfile
import unittest
from pathlib import Path

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

    async def check_once(self, targets):
        self.checked_targets = targets

    def stop_all(self):
        self.stop_all_calls += 1


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
            self.assertIs(scheduler.checked_targets, controller.targets)

            reloaded = UrlStore(url_file).load()
            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].url, "https://live.douyin.com/123")
            self.assertEqual(reloaded[0].quality, Quality.HIGH)
            self.assertEqual(reloaded[0].display_name, "anchor-a")

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


if __name__ == "__main__":
    unittest.main()
