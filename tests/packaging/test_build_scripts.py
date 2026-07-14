import ast
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertIn('Get-Command ffmpeg -CommandType Application', script)
        self.assertIn('--add-binary "$FFmpegPath;."', script)
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
        self.assertNotIn("collect_submodules", hook)
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

    def test_build_venv_is_ignored_once(self):
        matches = [
            line
            for line in self.gitignore_lines
            if re.fullmatch(r"\s*\.build-venv/\s*", line)
        ]

        self.assertEqual(matches, [".build-venv/"])

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

    def test_readme_and_package_metadata_describe_the_refactored_project(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        metadata = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("# Lubo 直播录制", readme)
        self.assertIn("zhangzhimiao1994/lubo-", metadata)
        self.assertNotIn("ihmily/DouyinLiveRecorder", readme)
        self.assertNotIn("ihmily/DouyinLiveRecorder", metadata)
        self.assertNotIn("已支持平台", readme)

    def test_packaged_config_sanitizer_removes_credentials_and_targets(self):
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
            config = output_dir.joinpath("config.ini").read_text(
                encoding="utf-8-sig"
            )

            self.assertEqual(
                output_dir.joinpath("URL_config.ini").read_text(
                    encoding="utf-8-sig"
                ),
                "",
            )
            section = ""
            for line in config.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped[1:-1]
                    continue
                if section in {"Cookie", "Authorization", "账号密码"} and "=" in line:
                    self.assertEqual(line.split("=", 1)[1].strip(), "")

    def test_source_tree_contains_no_embedded_login_cookie(self):
        sensitive_tokens = {
            "__ac_signature=",
            "passport_csrf_token",
            "login_status",
            "sessionid_ss",
            "ttwid=",
        }
        findings = []
        for path in (REPO_ROOT / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        is_cookie_header = (
                            isinstance(key, ast.Constant)
                            and isinstance(key.value, str)
                            and key.value.lower() == "cookie"
                        )
                        if (
                            is_cookie_header
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)
                            and value.value
                        ):
                            findings.append((path.relative_to(REPO_ROOT), value.lineno))
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                normalized = node.value.lower()
                if any(token in normalized for token in sensitive_tokens):
                    findings.append((path.relative_to(REPO_ROOT), node.lineno))

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
