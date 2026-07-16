import io
import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from lubo.apps.desktop import main as desktop_main
from lubo.core.events import RecorderEvent, RecorderEventType


class DesktopLoggingTests(unittest.TestCase):
    def test_windows_kivy_environment_disables_buggy_native_input_providers(self):
        with patch.dict(
            os.environ,
            {
                "KCFG_INPUT_WM_PEN": "wm_pen",
                "KCFG_INPUT_WM_TOUCH": "wm_touch",
            },
        ):
            desktop_main._configure_kivy_environment("win32")

            self.assertEqual(os.environ["KCFG_INPUT_WM_PEN"], "")
            self.assertEqual(os.environ["KCFG_INPUT_WM_TOUCH"], "")

    def test_non_windows_kivy_environment_preserves_input_providers(self):
        with patch.dict(
            os.environ,
            {
                "KCFG_INPUT_WM_PEN": "wm_pen",
                "KCFG_INPUT_WM_TOUCH": "wm_touch",
            },
        ):
            desktop_main._configure_kivy_environment("linux")

            self.assertEqual(os.environ["KCFG_INPUT_WM_PEN"], "wm_pen")
            self.assertEqual(os.environ["KCFG_INPUT_WM_TOUCH"], "wm_touch")

    def test_configure_file_logging_creates_bounded_rotating_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            handler = desktop_main._configure_file_logging(data_dir)
            try:
                self.assertIsInstance(handler, RotatingFileHandler)
                self.assertEqual(handler.maxBytes, 5 * 1024 * 1024)
                self.assertEqual(handler.backupCount, 3)

                logging.getLogger("lubo.test").info("desktop-log-probe")
                handler.flush()

                log_text = (data_dir / "logs" / "lubo.log").read_text(
                    encoding="utf-8"
                )
                self.assertIn("INFO", log_text)
                self.assertIn("desktop-log-probe", log_text)
            finally:
                desktop_main._close_file_logging(handler)

    def test_event_log_excludes_sensitive_payload(self):
        event = RecorderEvent(
            type=RecorderEventType.RECORDING_STARTED,
            target_id="target-123",
            message=(
                "failed https://pull.example/live?token=url-secret "
                "Cookie: sessionid=cookie-secret"
            ),
            payload={
                "command": ["ffmpeg", "https://pull.example/live?token=secret"]
            },
        )

        with self.assertLogs("lubo.apps.desktop.main", level="INFO") as captured:
            desktop_main._log_recorder_event(event)

        output = "\n".join(captured.output)
        self.assertIn("recording_started", output)
        self.assertIn("target-123", output)
        self.assertIn("<redacted-url>", output)
        self.assertIn("Cookie: <redacted>", output)
        self.assertNotIn("url-secret", output)
        self.assertNotIn("cookie-secret", output)
        self.assertNotIn("token=secret", output)
        self.assertNotIn("command", output)

    def test_file_handler_sanitizes_every_lubo_log_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            handler = desktop_main._configure_file_logging(data_dir)
            arbitrary_logger = logging.getLogger("lubo.resolver.test")
            observer_output = io.StringIO()
            observer = logging.StreamHandler(observer_output)
            package_logger = logging.getLogger("lubo")
            package_logger.addHandler(observer)
            try:
                arbitrary_logger.error(
                    "Authorization: Bearer %s access_token: %s",
                    "bearer-secret",
                    "access-secret",
                )
                arbitrary_logger.error("Cookie: sessionid=cookie-secret")
                arbitrary_logger.error(
                    "signed stream %s token=%s",
                    "hls://pull.example/live.m3u8?sign=url-secret",
                    "token-secret",
                )
                try:
                    raise RuntimeError(
                        "https://pull.example/live.flv?access_token=exception-secret"
                    )
                except RuntimeError:
                    arbitrary_logger.exception("resolver failed")
                handler.flush()

                log_text = (data_dir / "logs" / "lubo.log").read_text(
                    encoding="utf-8"
                )
                self.assertIn("Authorization: Bearer <redacted>", log_text)
                self.assertIn("access_token=<redacted>", log_text)
                self.assertIn("Cookie: <redacted>", log_text)
                self.assertIn("<redacted-url>", log_text)
                self.assertIn("token=<redacted>", log_text)
                for secret in (
                    "bearer-secret",
                    "access-secret",
                    "cookie-secret",
                    "url-secret",
                    "token-secret",
                    "exception-secret",
                ):
                    self.assertNotIn(secret, log_text)
                self.assertIn("bearer-secret", observer_output.getvalue())
            finally:
                package_logger.removeHandler(observer)
                observer.close()
                desktop_main._close_file_logging(handler)

    def test_main_closes_file_handler_after_app_exits(self):
        app = unittest.mock.MagicMock()
        app.user_data_dir = "C:/test/lubo"
        handler = unittest.mock.Mock()

        with (
            patch.object(desktop_main, "LuboDesktopApp", return_value=app),
            patch.object(
                desktop_main,
                "_configure_file_logging",
                return_value=handler,
            ) as configure,
            patch.object(desktop_main, "_close_file_logging") as close,
        ):
            desktop_main.main()

        configure.assert_called_once_with(Path("C:/test/lubo"))
        app.run.assert_called_once_with()
        close.assert_called_once_with(handler)


if __name__ == "__main__":
    unittest.main()
