import configparser
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_packaged_config import sanitize_config


REPO_ROOT = Path(__file__).resolve().parents[2]


class BuildScriptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.windows_script = (REPO_ROOT / "scripts" / "build_windows.ps1").read_text(
            encoding="utf-8"
        )
        cls.linux_script = (REPO_ROOT / "scripts" / "build_linux.sh").read_text(
            encoding="utf-8"
        )
        cls.gitignore_lines = (REPO_ROOT / ".gitignore").read_text(
            encoding="utf-8"
        ).splitlines()
        cls.desktop_workflow = (
            REPO_ROOT / ".github" / "workflows" / "build-desktop.yml"
        ).read_text(encoding="utf-8")

    def test_windows_build_uses_isolated_venv_python(self):
        script = self.windows_script

        self.assertIn('.build-venv/windows', script)
        self.assertIn('Scripts/python.exe', script)
        self.assertIn('base-python.fingerprint', script)
        self.assertIn('SHA256', script)
        self.assertRegex(script, r'\$CanonicalBasePython\s*=.*\.ToLowerInvariant\(\)')
        self.assertRegex(
            script,
            r'\$FingerprintSource\s*=\s*"\$CanonicalBasePython\|\$\(\$PythonInfo\.Version\)"',
        )
        self.assertRegex(
            script,
            r'\[string\]::Equals\(\s*\$StoredFingerprint,\s*\$BaseFingerprint',
        )
        self.assertRegex(
            script,
            r'Test-KivyCompatiblePython\s+\$BuildPythonInfo\.Version',
        )
        self.assertRegex(script, r'if\s*\(-not\s+\$VenvIsReusable\)')
        self.assertIn('[StringComparison]::OrdinalIgnoreCase', script)
        self.assertRegex(script, r'Remove-Item\s+-LiteralPath\s+\$BuildVenv\s+-Recurse')
        self.assertRegex(script, r'@\(\$BuildVenvRoot,\s*\$BuildVenv\)')
        self.assertIn('[IO.FileAttributes]::ReparsePoint', script)
        self.assertIn('Resolve-Path -LiteralPath $BuildVenvRoot', script)
        windows_remove = script.index('Remove-Item -LiteralPath $BuildVenv -Recurse')
        self.assertLess(script.index('[IO.FileAttributes]::ReparsePoint'), windows_remove)
        self.assertLess(
            script.index('Resolve-Path -LiteralPath $BuildVenvRoot'),
            windows_remove,
        )
        self.assertRegex(script, r'\[Text\.Encoding\]::ASCII')
        self.assertRegex(script, r'Move-Item\s+-LiteralPath\s+\$FingerprintTemp')
        self.assertRegex(script, r'&\s+\$BasePython\s+-m\s+venv\s+\$BuildVenv')
        self.assertRegex(script, r'&\s+\$BuildPython\s+-m\s+pip\s+install')
        self.assertRegex(script, r'&\s+\$BuildPython\s+-m\s+PyInstaller\b')
        self.assertNotRegex(script, r'&\s+\$BasePython\s+-m\s+(?:pip|PyInstaller)\b')
        self.assertNotIn('--collect-submodules kivy', script)
        self.assertIn('--collect-data kivy', script)
        self.assertIn('$env:FFMPEG_PATH', script)
        self.assertIn('Get-Command ffmpeg -CommandType Application', script)
        self.assertIn('--add-data "$FFmpegPath;."', script)
        self.assertNotIn('--add-binary "$FFmpegPath;."', script)
        self.assertIn('throw "FFmpeg was not found on PATH.', script)
        self.assertIn('scripts/prepare_packaged_config.py', script)
        self.assertIn('--add-data "$PackagedConfig;config"', script)
        self.assertNotIn('--add-data "config;config"', script)

    def test_linux_build_uses_isolated_venv_python(self):
        script = self.linux_script

        self.assertIn('.build-venv/linux', script)
        self.assertIn('bin/python', script)
        self.assertIn('base-python.fingerprint', script)
        self.assertIn('os.path.realpath(sys.executable)', script)
        self.assertIn('hashlib.sha256', script)
        self.assertRegex(
            script,
            r'FINGERPRINT_SOURCE="\$\{PYTHON_PATH\}\|\$\{PYTHON_VERSION\}"',
        )
        self.assertRegex(
            script,
            r'\[\[\s+"\$STORED_FINGERPRINT"\s+==\s+"\$BASE_FINGERPRINT"\s+\]\]',
        )
        self.assertRegex(
            script,
            r'is_kivy_compatible_version\s+"\$BUILD_PYTHON_VERSION"',
        )
        self.assertRegex(script, r'if\s+\[\[\s+"\$VENV_REUSABLE"\s+!=\s+"1"\s+\]\]')
        self.assertRegex(
            script,
            r'if\s+\[\[\s+"\$BUILD_VENV"\s+!=\s+"\$EXPECTED_BUILD_VENV"\s+\]\]',
        )
        self.assertRegex(script, r'rm\s+-rf\s+--\s+"\$BUILD_VENV"')
        self.assertRegex(
            script,
            r'\[\[\s+-L\s+"\$BUILD_VENV_ROOT"\s+\|\|\s+-L\s+"\$BUILD_VENV"\s+\]\]',
        )
        self.assertIn('realpath -e -- "$REPO_ROOT"', script)
        self.assertIn('realpath -e -- "$BUILD_VENV_ROOT"', script)
        self.assertIn('realpath -e -- "$BUILD_VENV"', script)
        linux_remove = script.index('rm -rf -- "$BUILD_VENV"')
        self.assertLess(script.index('[[ -L "$BUILD_VENV_ROOT"'), linux_remove)
        self.assertLess(script.index('realpath -e -- "$BUILD_VENV"'), linux_remove)
        self.assertRegex(script, r'mv\s+-f\s+--\s+"\$FINGERPRINT_TEMP"')
        self.assertRegex(script, r'"\$PYTHON_BIN"\s+-m\s+venv\s+"\$BUILD_VENV"')
        self.assertRegex(script, r'"\$BUILD_PYTHON"\s+-m\s+pip\s+install')
        self.assertRegex(script, r'"\$BUILD_PYTHON"\s+-m\s+PyInstaller\b')
        self.assertNotRegex(script, r'"\$PYTHON_BIN"\s+-m\s+(?:pip|PyInstaller)\b')
        self.assertNotIn('--collect-submodules kivy', script)
        self.assertIn('--collect-data kivy', script)
        self.assertIn('FFMPEG_PATH="$(command -v ffmpeg)"', script)
        self.assertIn('--add-binary "$FFMPEG_PATH:."', script)
        self.assertIn('FFmpeg was not found on PATH.', script)
        self.assertIn('scripts/prepare_packaged_config.py', script)
        self.assertIn('--add-data "$PACKAGED_CONFIG:config"', script)
        self.assertNotIn('--add-data "config:config"', script)

    def test_desktop_build_uses_bounded_kivy_hook(self):
        hook_path = REPO_ROOT / "packaging" / "pyinstaller-hooks" / "hook-kivy.py"

        self.assertTrue(hook_path.is_file(), "A project Kivy hook must bound provider discovery")
        hook = hook_path.read_text(encoding="utf-8")
        for script in (self.windows_script, self.linux_script):
            self.assertIn('--additional-hooks-dir "packaging/pyinstaller-hooks"', script)
        self.assertNotIn("get_deps_all", hook)
        self.assertNotIn("get_factory_modules", hook)
        self.assertIn('collect_submodules("kivy.graphics")', hook)
        self.assertNotIn('collect_submodules("kivy.core")', hook)
        self.assertIn("datas = [", hook)
        self.assertIn("excludedimports =", hook)
        self.assertIn("kivy.core.window.window_sdl2", hook)
        self.assertIn("kivy.core.text.text_sdl2", hook)
        self.assertIn("kivy.core.image.img_sdl2", hook)
        self.assertIn("kivy.core.clipboard.clipboard_winctypes", hook)

    def test_desktop_build_keeps_kivy_from_amplifying_pyinstaller_logs(self):
        self.assertIn('$env:KIVY_LOG_MODE = "PYTHON"', self.windows_script)
        self.assertIn('$env:KIVY_NO_FILELOG = "1"', self.windows_script)
        self.assertIn('title=Windows build phase', self.windows_script)
        self.assertRegex(
            self.linux_script,
            r'KIVY_LOG_MODE=PYTHON\s+\\\s*KIVY_NO_FILELOG=1',
        )
        self.assertIn("--log-level INFO", self.windows_script)
        self.assertIn("--log-level INFO", self.linux_script)

    def test_windows_build_disables_kivy_window_initialization_during_analysis(self):
        self.assertIn('$env:KIVY_DOC = "1"', self.windows_script)
        self.assertIn('Remove-Item Env:KIVY_DOC', self.windows_script)
        self.assertLess(
            self.windows_script.index('$env:KIVY_DOC = "1"'),
            self.windows_script.index('& $BuildPython -m PyInstaller'),
        )

    def test_build_venv_is_ignored_once(self):
        matches = [
            line
            for line in self.gitignore_lines
            if re.fullmatch(r"\s*\.build-venv/\s*", line)
        ]

        self.assertEqual(matches, [".build-venv/"])

    def test_project_uses_only_the_lubo_python_package(self):
        legacy_package = "douyinlive" + "recorder"

        self.assertTrue((REPO_ROOT / "lubo" / "__init__.py").is_file())
        self.assertFalse((REPO_ROOT / legacy_package).exists())
        tracked = subprocess.run(
            [
                "git",
                "grep",
                "-l",
                legacy_package,
                "--",
                "*.py",
                "*.sh",
                "*.ps1",
                "*.yml",
                "*.toml",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(tracked.returncode, 1, tracked.stderr)
        self.assertEqual(tracked.stdout.strip(), "")

    def test_desktop_ci_builds_and_uploads_windows_and_linux(self):
        workflow = self.desktop_workflow

        self.assertIn("runs-on: windows-2022", workflow)
        self.assertEqual(workflow.count('python-version: "3.12"'), 2)
        self.assertIn("scripts/build_windows.ps1", workflow)
        self.assertIn("name: DouyinLiveRecorder-windows", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("scripts/build_linux.sh", workflow)
        self.assertIn("xvfb-run", workflow)
        self.assertIn("name: DouyinLiveRecorder-linux", workflow)
        self.assertIn("title=Linux build log", workflow)
        self.assertEqual(workflow.count("actions/upload-artifact@v4"), 2)

    def test_windows_ci_separates_dependency_setup_from_packaging(self):
        self.assertIn("[switch]$PrepareOnly", self.windows_script)
        self.assertIn("if ($PrepareOnly)", self.windows_script)
        self.assertIn("Prepare Windows build environment", self.desktop_workflow)
        self.assertIn("-PrepareOnly", self.desktop_workflow)
        self.assertIn("-SkipInstall", self.desktop_workflow)
        self.assertIn("$env:ChocolateyInstall", self.desktop_workflow)
        self.assertIn("FFMPEG_PATH", self.desktop_workflow)
        self.assertIn("id: windows-build", self.desktop_workflow)
        self.assertIn("timeout-minutes: 12", self.desktop_workflow)
        self.assertIn("title=Windows build log", self.desktop_workflow)

    def test_release_workflow_publishes_all_three_platform_artifacts(self):
        workflow_path = REPO_ROOT / ".github" / "workflows" / "publish-release.yml"

        self.assertTrue(workflow_path.is_file(), "A release publishing workflow is required")
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("Build Desktop Apps", workflow)
        self.assertIn("Build Android APK", workflow)
        self.assertIn("DouyinLiveRecorder-windows", workflow)
        self.assertIn("DouyinLiveRecorder-linux", workflow)
        self.assertIn("DouyinLiveRecorder-android-debug", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)

    def test_readme_and_package_metadata_describe_the_refactored_project(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        metadata = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("# Lubo 直播录制", readme)
        self.assertIn("zhangzhimiao1994/lubo-", metadata)
        self.assertNotIn("ihmily/DouyinLiveRecorder", readme)
        self.assertNotIn("ihmily/DouyinLiveRecorder", metadata)
        self.assertNotIn("已支持平台", readme)

    def test_config_template_contains_only_the_current_schema(self):
        parser = configparser.ConfigParser()
        parser.read(REPO_ROOT / "config" / "config.ini", encoding="utf-8-sig")

        self.assertEqual(parser.sections(), ["recorder", "monitor", "proxy", "cookies"])
        self.assertEqual(tuple(parser["cookies"]), ("douyin", "bilibili", "huya", "douyu"))
        self.assertTrue(all(value == "" for value in parser["cookies"].values()))

    def test_packaged_config_sanitizer_clears_targets_and_all_cookie_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "source"
            output_dir = Path(tmp) / "config"
            source_dir.mkdir()
            source = source_dir / "config.ini"
            source_content = (
                "[DEFAULT]\n"
                "convert_to_mp4 = SECRET_DEFAULT\n"
                "[recorder]\n"
                "save_path = D:/private/recordings\n"
                "output_format = mkv\n"
                "quality = high\n"
                "split_enabled = false\n"
                "split_seconds = 600\n"
                "[monitor]\n"
                "loop_seconds = 45\n"
                "max_concurrency = 5\n"
                "[proxy]\n"
                "enabled = true\n"
                "address = proxy.example:8080\n"
                "[cookies]\n"
                "douyin = dy-secret\n"
                "bilibili = bili-secret\n"
                "future_platform = future-secret\n"
                "[Cookie]\n"
                "legacy = SECRET_LEGACY_COOKIE\n"
                "[CoOkIeS]\n"
                "variant = SECRET_CASE_VARIANT\n"
                "[Authorization]\n"
                "token = TOKEN_AUTHORIZATION\n"
                "[账号密码]\n"
                "password = PASSWORD_ACCOUNT\n"
                "[unknown]\n"
                "credential = SECRET_UNKNOWN\n"
            )
            source.write_text(source_content, encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "prepare_packaged_config.py"),
                    "--source",
                    str(source_dir),
                    "--output",
                    str(output_dir),
                ],
                check=True,
            )

            packaged_path = output_dir / "config.ini"
            packaged_text = packaged_path.read_text(encoding="utf-8-sig")
            parser = configparser.ConfigParser()
            parser.read_string(packaged_text)
            self.assertEqual((output_dir / "URL_config.ini").read_text(encoding="utf-8-sig"), "")
            self.assertEqual(source.read_text(encoding="utf-8"), source_content)
            self.assertEqual(parser.sections(), ["recorder", "monitor", "proxy", "cookies"])
            self.assertEqual(
                set(parser["recorder"]),
                {
                    "save_path",
                    "output_format",
                    "quality",
                    "split_enabled",
                    "split_seconds",
                    "convert_to_mp4",
                },
            )
            self.assertEqual(set(parser["monitor"]), {"loop_seconds", "max_concurrency"})
            self.assertEqual(set(parser["proxy"]), {"enabled", "address"})
            self.assertEqual(set(parser["cookies"]), {"douyin", "bilibili", "huya", "douyu"})
            self.assertEqual(parser["recorder"]["save_path"], "")
            self.assertEqual(parser["recorder"]["output_format"], "mkv")
            self.assertEqual(parser["recorder"]["quality"], "high")
            self.assertEqual(parser["recorder"]["convert_to_mp4"], "true")
            self.assertEqual(parser["monitor"]["loop_seconds"], "45")
            self.assertEqual(parser["proxy"]["address"], "proxy.example:8080")
            self.assertTrue(all(value == "" for value in parser["cookies"].values()))
            for marker in ("SECRET", "TOKEN", "PASSWORD"):
                self.assertNotIn(marker, packaged_text)

    def test_packaged_config_sanitizer_rejects_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "output" / "config.ini"

            with self.assertRaises(FileNotFoundError):
                sanitize_config(Path(tmp) / "missing.ini", destination)

            self.assertFalse(destination.exists())

    def test_packaged_config_sanitizer_accepts_repository_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "config"

            subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "prepare_packaged_config.py"),
                    "--source",
                    str(REPO_ROOT / "config"),
                    "--output",
                    str(output_dir),
                ],
                check=True,
            )

            parser = configparser.ConfigParser()
            parser.read(output_dir / "config.ini", encoding="utf-8-sig")
            self.assertEqual(parser.sections(), ["recorder", "monitor", "proxy", "cookies"])
            self.assertEqual(tuple(parser["cookies"]), ("douyin", "bilibili", "huya", "douyu"))
            self.assertTrue(all(value == "" for value in parser["cookies"].values()))
            self.assertEqual((output_dir / "URL_config.ini").read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
