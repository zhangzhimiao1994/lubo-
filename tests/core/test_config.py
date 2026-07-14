import tempfile
import unittest
from pathlib import Path

from douyinliverecorder.core.config import AppConfig, ConfigService
from douyinliverecorder.core.models import OutputFormat, Quality


class ConfigServiceTests(unittest.TestCase):
    def test_loads_existing_chinese_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[录制设置]\n"
                "直播保存路径(不填则默认) = D:/videos\n"
                "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频 = mp4\n"
                "原画|超清|高清|标清|流畅 = 高清\n"
                "循环时间(秒) = 60\n"
                "同一时间访问网络的线程数 = 2\n"
                "是否使用代理ip(是/否) = 是\n"
                "代理地址 = 127.0.0.1:7890\n",
                encoding="utf-8-sig",
            )

            config = ConfigService(path).load()

            self.assertEqual(config.save_path, "D:/videos")
            self.assertEqual(config.output_format, OutputFormat.MP4)
            self.assertEqual(config.quality, Quality.HIGH)
            self.assertEqual(config.loop_seconds, 60)
            self.assertEqual(config.max_concurrency, 2)
            self.assertTrue(config.use_proxy)
            self.assertEqual(config.proxy_addr, "127.0.0.1:7890")

    def test_loads_douyin_cookie_from_cookie_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[录制设置]\n"
                "原画|超清|高清|标清|流畅 = 高清\n"
                "[Cookie]\n"
                "抖音cookie = sessionid=test-cookie; ttwid=test\n",
                encoding="utf-8-sig",
            )

            config = ConfigService(path).load()

            self.assertEqual(config.douyin_cookie, "sessionid=test-cookie; ttwid=test")

    def test_missing_file_returns_defaults_and_creates_file_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)

            config = service.load()
            service.save(config)

            self.assertEqual(config.output_format, OutputFormat.TS)
            self.assertEqual(config.quality, Quality.ORIGINAL)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("录制设置", content)
            self.assertIn("循环时间(秒)", content)

    def test_save_updates_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)
            service.save(AppConfig(save_path="E:/recordings", loop_seconds=30, use_proxy=True, proxy_addr="host:8080"))

            reloaded = service.load()

            self.assertEqual(reloaded.save_path, "E:/recordings")
            self.assertEqual(reloaded.loop_seconds, 30)
            self.assertTrue(reloaded.use_proxy)
            self.assertEqual(reloaded.proxy_addr, "host:8080")

    def test_save_preserves_comments_unknown_keys_and_unknown_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "# top comment\n"
                "[录制设置]\n"
                "# keep this comment\n"
                "循环时间(秒) = 60\n"
                "MixedCaseKey = KeepMe\n"
                "[Cookie]\n"
                "B站cookie = abc\n"
                "SMTP邮件服务器 = smtp.example.com\n",
                encoding="utf-8-sig",
            )
            service = ConfigService(path)
            config = service.load()
            config.loop_seconds = 45

            service.save(config)

            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("# top comment", content)
            self.assertIn("# keep this comment", content)
            self.assertIn("MixedCaseKey = KeepMe", content)
            self.assertIn("B站cookie = abc", content)
            self.assertIn("SMTP邮件服务器 = smtp.example.com", content)
            self.assertIn("循环时间(秒) = 45", content)

    def test_save_appends_missing_known_key_before_next_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[录制设置]\n"
                "循环时间(秒) = 60\n"
                "[Cookie]\n"
                "B站cookie = abc\n",
                encoding="utf-8-sig",
            )
            service = ConfigService(path)
            config = service.load()
            config.proxy_addr = "127.0.0.1:7890"

            service.save(config)

            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("代理地址 = 127.0.0.1:7890", content)
            self.assertLess(content.index("代理地址 = 127.0.0.1:7890"), content.index("[Cookie]"))

    def test_malformed_true_default_bools_keep_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[录制设置]\n"
                "分段录制是否开启 = maybe\n"
                "录制完成后自动转为mp4格式 = maybe\n",
                encoding="utf-8-sig",
            )

            config = ConfigService(path).load()

            self.assertTrue(config.split_enabled)
            self.assertTrue(config.convert_to_mp4)


if __name__ == "__main__":
    unittest.main()
