import tempfile
import unittest
from pathlib import Path

from lubo.core.models import Quality
from lubo.core.url_store import UrlStore


class UrlStoreTests(unittest.TestCase):
    def test_loads_plain_quality_and_name_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            path.write_text(
                "https://live.douyin.com/111\n"
                "高清,https://live.douyin.com/222\n"
                "原画,https://live.douyin.com/333,主播三\n"
                "#https://live.douyin.com/444\n",
                encoding="utf-8-sig",
            )

            targets = UrlStore(path).load()

            self.assertEqual(len(targets), 4)
            self.assertEqual(targets[0].quality, Quality.ORIGINAL)
            self.assertEqual(targets[1].quality, Quality.HIGH)
            self.assertEqual(targets[2].display_name, "主播三")
            self.assertFalse(targets[3].enabled)

    def test_save_round_trips_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            store = UrlStore(path)
            targets = store.load_from_lines(["高清,https://live.douyin.com/222,主播二"])

            store.save(targets)
            reloaded = store.load()

            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].url, "https://live.douyin.com/222")
            self.assertEqual(reloaded[0].quality, Quality.HIGH)
            self.assertEqual(reloaded[0].display_name, "主播二")

    def test_loads_full_width_comma_quality_url_line(self):
        store = UrlStore("URL_config.ini")

        targets = store.load_from_lines(["超清，https://live.douyin.com/222"])

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].quality, Quality.ULTRA)
        self.assertEqual(targets[0].url, "https://live.douyin.com/222")

    def test_load_skips_duplicate_after_url_normalization(self):
        store = UrlStore("URL_config.ini")

        targets = store.load_from_lines(
            ["https://live.douyin.com/111", "live.douyin.com/111"]
        )

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].url, "https://live.douyin.com/111")

    def test_loads_disabled_quality_url_name_line(self):
        store = UrlStore("URL_config.ini")

        targets = store.load_from_lines(["#高清,https://live.douyin.com/333,主播三"])

        self.assertEqual(len(targets), 1)
        self.assertFalse(targets[0].enabled)
        self.assertEqual(targets[0].quality, Quality.HIGH)
        self.assertEqual(targets[0].url, "https://live.douyin.com/333")
        self.assertEqual(targets[0].display_name, "主播三")

    def test_unknown_quality_before_url_falls_back_to_original(self):
        store = UrlStore("URL_config.ini")

        targets = store.load_from_lines(["4K,https://live.douyin.com/444,主播四"])

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].url, "https://live.douyin.com/444")
        self.assertEqual(targets[0].quality, Quality.ORIGINAL)
        self.assertEqual(targets[0].display_name, "主播四")

    def test_save_removes_temp_file_and_reloads_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            store = UrlStore(path)
            targets = store.load_from_lines(["高清,https://live.douyin.com/555,主播五"])

            store.save(targets)
            reloaded = store.load()

            self.assertFalse(path.with_name(f"{path.name}.tmp").exists())
            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].url, "https://live.douyin.com/555")
            self.assertEqual(reloaded[0].quality, Quality.HIGH)
            self.assertEqual(reloaded[0].display_name, "主播五")

    def test_add_skips_duplicate_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            store = UrlStore(path)
            first = store.load_from_lines(["https://live.douyin.com/111"])
            second = store.add(first, "live.douyin.com/111")

            self.assertEqual(len(second), 1)

    def test_plain_and_unknown_quality_lines_use_configured_default(self):
        store = UrlStore("URL_config.ini", default_quality=Quality.HIGH)

        targets = store.load_from_lines(
            [
                "https://live.douyin.com/111",
                "4K,https://live.douyin.com/222,anchor",
            ]
        )

        self.assertEqual([target.quality for target in targets], [Quality.HIGH, Quality.HIGH])

    def test_add_without_quality_uses_configured_default(self):
        store = UrlStore("URL_config.ini", default_quality=Quality.ULTRA)

        targets = store.add([], "https://live.douyin.com/111")

        self.assertEqual(targets[0].quality, Quality.ULTRA)


if __name__ == "__main__":
    unittest.main()
