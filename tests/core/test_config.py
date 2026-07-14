import configparser
import tempfile
import unittest
from pathlib import Path

from lubo.core.config import AppConfig, ConfigService
from lubo.core.models import OutputFormat, Quality


PLATFORM_KEYS = ("douyin", "bilibili", "huya", "douyu")


class ConfigServiceTests(unittest.TestCase):
    def write_config(self, directory: str, content: str) -> Path:
        path = Path(directory) / "config.ini"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_all_sections_and_only_supported_platform_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(
                tmp,
                "[recorder]\n"
                "save_path = D:/videos\n"
                "output_format = mp4\n"
                "quality = HIGH\n"
                "split_enabled = no\n"
                "split_seconds = 900\n"
                "convert_to_mp4 = 0\n"
                "[monitor]\n"
                "loop_seconds = 60\n"
                "max_concurrency = 2\n"
                "[proxy]\n"
                "enabled = yes\n"
                "address = 127.0.0.1:7890\n"
                "[cookies]\n"
                "douyin = dy-cookie\n"
                "bilibili = bili-cookie\n"
                "huya = huya-cookie\n"
                "douyu = douyu-cookie\n"
                "future_platform = ignored\n",
            )

            config = ConfigService(path).load()

            self.assertEqual(config.save_path, "D:/videos")
            self.assertEqual(config.output_format, OutputFormat.MP4)
            self.assertEqual(config.quality, Quality.HIGH)
            self.assertFalse(config.split_enabled)
            self.assertEqual(config.split_seconds, 900)
            self.assertFalse(config.convert_to_mp4)
            self.assertEqual(config.loop_seconds, 60)
            self.assertEqual(config.max_concurrency, 2)
            self.assertTrue(config.use_proxy)
            self.assertEqual(config.proxy_addr, "127.0.0.1:7890")
            self.assertEqual(
                config.cookies,
                {
                    "douyin": "dy-cookie",
                    "bilibili": "bili-cookie",
                    "huya": "huya-cookie",
                    "douyu": "douyu-cookie",
                },
            )

    def test_loaded_cookies_are_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(tmp, "[cookies]\ndouyin = dy-cookie\n")

            cookies = ConfigService(path).load().cookies

            with self.assertRaises(TypeError):
                cookies["douyin"] = "replacement"  # type: ignore[index]

    def test_app_config_defensively_copies_cookies(self):
        supplied = {"douyin": "original", "future_platform": "ignored"}

        config = AppConfig(cookies=supplied)
        supplied["douyin"] = "changed"

        self.assertEqual(
            config.cookies,
            {
                "douyin": "original",
                "bilibili": "",
                "huya": "",
                "douyu": "",
            },
        )
        with self.assertRaises(TypeError):
            config.cookies["douyin"] = "replacement"  # type: ignore[index]

    def test_app_config_default_cookies_have_exact_platform_keys(self):
        config = AppConfig()

        self.assertEqual(config.cookies, dict.fromkeys(PLATFORM_KEYS, ""))
        with self.assertRaises(TypeError):
            config.cookies["douyin"] = "replacement"  # type: ignore[index]

    def test_missing_file_returns_defaults_and_save_creates_new_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)

            config = service.load()
            service.save(config)

            self.assertEqual(config.save_path, "")
            self.assertEqual(config.output_format, OutputFormat.TS)
            self.assertEqual(config.quality, Quality.ORIGINAL)
            self.assertEqual(config.loop_seconds, 300)
            self.assertEqual(config.max_concurrency, 3)
            self.assertFalse(config.use_proxy)
            self.assertEqual(config.proxy_addr, "")
            self.assertTrue(config.split_enabled)
            self.assertEqual(config.split_seconds, 1800)
            self.assertTrue(config.convert_to_mp4)
            self.assertEqual(config.cookies, dict.fromkeys(PLATFORM_KEYS, ""))
            parser = configparser.ConfigParser()
            parser.read(path, encoding="utf-8-sig")
            self.assertEqual(parser.sections(), ["recorder", "monitor", "proxy", "cookies"])
            self.assertEqual(tuple(parser["cookies"]), PLATFORM_KEYS)

    def test_missing_cookie_values_are_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(tmp, "[cookies]\nhuya = set\n")

            config = ConfigService(path).load()

            self.assertEqual(
                config.cookies,
                {"douyin": "", "bilibili": "", "huya": "set", "douyu": ""},
            )

    def test_save_and_reload_preserves_all_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)
            expected = AppConfig(
                save_path="E:/recordings",
                output_format=OutputFormat.MKV,
                quality=Quality.ULTRA,
                loop_seconds=45,
                max_concurrency=5,
                use_proxy=True,
                proxy_addr="host:8080",
                split_enabled=False,
                split_seconds=600,
                convert_to_mp4=False,
                cookies={
                    "douyin": "dy",
                    "bilibili": "bili",
                    "huya": "hy",
                    "douyu": "doyu",
                },
            )

            service.save(expected)

            self.assertEqual(service.load(), expected)

    def test_save_and_reload_normalizes_partial_and_extra_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = ConfigService(Path(tmp) / "config.ini")
            expected = AppConfig(
                cookies={"bilibili": "bili", "future_platform": "ignored"}
            )

            service.save(expected)

            self.assertEqual(service.load(), expected)
            self.assertEqual(
                expected.cookies,
                {"douyin": "", "bilibili": "bili", "huya": "", "douyu": ""},
            )

    def test_save_rewrites_existing_file_to_only_the_current_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(
                tmp,
                "[legacy]\ncredential = secret\n[cookies]\nfuture = remove-me\n",
            )

            ConfigService(path).save(AppConfig(cookies={"douyin": "dy"}))

            parser = configparser.ConfigParser()
            parser.read(path, encoding="utf-8-sig")
            self.assertEqual(parser.sections(), ["recorder", "monitor", "proxy", "cookies"])
            self.assertEqual(dict(parser["cookies"]), {
                "douyin": "dy",
                "bilibili": "",
                "huya": "",
                "douyu": "",
            })

    def test_quality_accepts_names_case_insensitively_and_enum_values(self):
        cases = {
            "original": Quality.ORIGINAL,
            "BLUE_RAY": Quality.BLUE_RAY,
            "Ultra": Quality.ULTRA,
            "high": Quality.HIGH,
            "STANDARD": Quality.STANDARD,
            "smooth": Quality.SMOOTH,
            Quality.HIGH.value: Quality.HIGH,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            for raw, expected in cases.items():
                with self.subTest(raw=raw):
                    path.write_text(f"[recorder]\nquality = {raw}\n", encoding="utf-8")
                    self.assertEqual(ConfigService(path).load().quality, expected)

    def test_boolean_spellings_are_case_insensitive(self):
        true_values = ("true", "YES", "1")
        false_values = ("false", "No", "0")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            for raw in true_values:
                with self.subTest(raw=raw):
                    path.write_text(f"[proxy]\nenabled = {raw}\n", encoding="utf-8")
                    self.assertTrue(ConfigService(path).load().use_proxy)
            for raw in false_values:
                with self.subTest(raw=raw):
                    path.write_text(f"[recorder]\nsplit_enabled = {raw}\n", encoding="utf-8")
                    self.assertFalse(ConfigService(path).load().split_enabled)

    def test_invalid_values_fall_back_to_field_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(
                tmp,
                "[recorder]\n"
                "output_format = wav\n"
                "quality = impossible\n"
                "split_enabled = maybe\n"
                "split_seconds = soon\n"
                "convert_to_mp4 = perhaps\n"
                "[monitor]\n"
                "loop_seconds = later\n"
                "max_concurrency = many\n"
                "[proxy]\n"
                "enabled = sometimes\n",
            )

            config = ConfigService(path).load()

            self.assertEqual(config.output_format, OutputFormat.TS)
            self.assertEqual(config.quality, Quality.ORIGINAL)
            self.assertTrue(config.split_enabled)
            self.assertEqual(config.split_seconds, 1800)
            self.assertTrue(config.convert_to_mp4)
            self.assertEqual(config.loop_seconds, 300)
            self.assertEqual(config.max_concurrency, 3)
            self.assertFalse(config.use_proxy)


if __name__ == "__main__":
    unittest.main()
