import configparser
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from scripts.prepare_packaged_config import sanitize_config


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_REFERENCE_PATHS = (
    "src",
    "i18n",
    "main.py",
    "demo.py",
    "msg_push.py",
    "ffmpeg_install.py",
    "i18n.py",
    "index.html",
    "StopRecording.vbs",
    "Dockerfile",
    "docker-compose.yaml",
    ".dockerignore",
)

REMOVED_DOCUMENTS = (
    "docs/cross-platform-apps.md",
    "docs/superpowers/specs/2026-07-14-cross-platform-recorder-design.md",
    "docs/superpowers/plans/2026-07-14-core-douyin-desktop-vertical-slice.md",
    "docs/superpowers/specs/2026-07-14-lubo-multi-platform-clean-room-design.md",
    "docs/superpowers/plans/2026-07-14-lubo-multi-platform-clean-room.md",
)

CURRENT_REPOSITORY = "https://github.com/zhangzhimiao1994/lubo-"
HISTORICAL_NOTICE_PATH = "THIRD_PARTY_NOTICES.md"
HISTORICAL_AUTHOR = "Hm" + "ily"

MIT_LICENSE_BODY = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def mit_license(copyright_line):
    return f"MIT License\n\n{copyright_line}\n\n{MIT_LICENSE_BODY}"


def versions_matching_specifier(specifier, versions):
    script = (
        "import sys; "
        "from packaging.specifiers import SpecifierSet; "
        "from packaging.version import Version; "
        "allowed = SpecifierSet(sys.argv[1]); "
        "print(','.join(v for v in sys.argv[2:] if Version(v) in allowed))"
    )
    output = subprocess.check_output(
        [sys.executable, "-c", script, specifier, *versions],
        cwd=REPO_ROOT,
        text=True,
    )
    return output.strip().split(",") if output.strip() else []


def forbidden_identity_tokens():
    return {
        "old repository URL": (
            "https://github.com/" + "ih" + "mily/" + "DouyinLive" + "Recorder"
        ),
        "old CamelCase application name": "DouyinLive" + "Recorder",
        "old lowercase package name": "douyinlive" + "recorder",
        "old Android package": "org." + "douyin" + "recorder",
        "legacy entrypoint phrase": "legacy " + "main.py",
        "old author": HISTORICAL_AUTHOR,
    }


def decode_tracked_text(relative_path, content):
    if b"\0" in content:
        raise ValueError(f"{relative_path}: tracked text contains NUL bytes")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{relative_path}: tracked text is not valid UTF-8") from error


def identity_violations(relative_path, content):
    violations = []
    folded_content = content.casefold()
    for label, token in forbidden_identity_tokens().items():
        if label == "old author" and relative_path == HISTORICAL_NOTICE_PATH:
            if content.count(HISTORICAL_AUTHOR) != 1:
                violations.append(label)
                continue
            remaining = content.replace(HISTORICAL_AUTHOR, "", 1)
            if token.casefold() in remaining.casefold():
                violations.append(label)
            continue
        if token.casefold() in folded_content:
            violations.append(label)
    return violations


def tracked_text_files():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
    )
    for raw_relative_path in output.split(b"\0"):
        if not raw_relative_path:
            continue
        relative_path = raw_relative_path.decode("utf-8")
        path = REPO_ROOT / relative_path
        content = path.read_bytes()
        yield relative_path, decode_tracked_text(relative_path, content)


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
        cls.desktop_app = (
            REPO_ROOT / "lubo" / "apps" / "desktop" / "main.py"
        ).read_text(encoding="utf-8")
        cls.android_app = (
            REPO_ROOT / "lubo" / "apps" / "android" / "main.py"
        ).read_text(encoding="utf-8")
        cls.android_entrypoint = (REPO_ROOT / "android" / "main.py").read_text(
            encoding="utf-8"
        )

    def test_legacy_reference_paths_are_removed(self):
        existing = [
            relative_path
            for relative_path in LEGACY_REFERENCE_PATHS
            if (REPO_ROOT / relative_path).exists()
        ]

        self.assertEqual(
            existing,
            [],
            "Legacy reference paths still exist:\n" + "\n".join(existing),
        )

    def test_windows_build_uses_isolated_venv_python(self):
        script = self.windows_script

        self.assertIn('$ResourceDirectories = @("config")', script)
        self.assertNotIn('src/javascript', script)
        self.assertNotIn('--add-data "i18n;i18n"', script)
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

        self.assertIn('RESOURCE_DIRECTORIES=("config")', script)
        self.assertNotIn('src/javascript', script)
        self.assertNotIn('--add-data "i18n:i18n"', script)
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

    def test_runtime_and_build_files_do_not_reference_removed_resources(self):
        forbidden_references = (
            "from src",
            "import src",
            "src/javascript",
            "i18n;i18n",
            "i18n:i18n",
            "REPO_ROOT/src",
        )
        scanned_roots = (
            REPO_ROOT / "lubo",
            REPO_ROOT / "android",
            REPO_ROOT / "scripts",
            REPO_ROOT / "packaging",
            REPO_ROOT / ".github" / "workflows",
        )
        scanned_suffixes = {".java", ".py", ".ps1", ".sh", ".spec", ".xml", ".yml"}
        violations = []

        for root in scanned_roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in scanned_suffixes:
                    continue
                content = path.read_text(encoding="utf-8")
                for reference in forbidden_references:
                    if reference in content:
                        violations.append(f"{path.relative_to(REPO_ROOT)}: {reference}")

        self.assertEqual(
            violations,
            [],
            "Removed runtime/resource references remain:\n" + "\n".join(violations),
        )

    def test_runtime_requirements_are_exactly_the_current_direct_dependencies(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()

        self.assertEqual(
            requirements,
            ["streamlink==8.4.0", "yt-dlp==2026.6.9"],
        )

    def test_desktop_ci_builds_and_uploads_windows_and_linux(self):
        workflow = self.desktop_workflow

        self.assertIn("runs-on: windows-2022", workflow)
        self.assertEqual(workflow.count('python-version: "3.12"'), 2)
        self.assertIn("scripts/build_windows.ps1", workflow)
        self.assertIn("name: Lubo-windows", workflow)
        self.assertIn("path: dist/Lubo", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("scripts/build_linux.sh", workflow)
        self.assertIn("xvfb-run", workflow)
        self.assertIn("name: Lubo-linux", workflow)
        self.assertIn("title=Linux build log", workflow)
        self.assertEqual(workflow.count("actions/upload-artifact@v4"), 2)

    def test_desktop_builds_and_entrypoint_use_lubo_name(self):
        self.assertIn("class LuboDesktopApp(App):", self.desktop_app)
        self.assertIn("LuboDesktopApp().run()", self.desktop_app)
        for script in (self.windows_script, self.linux_script):
            self.assertIn("--name Lubo", script)
            self.assertIn("dist/Lubo", script)

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
        self.assertIn("--name Lubo-windows", workflow)
        self.assertIn("--name Lubo-linux", workflow)
        self.assertIn("--name Lubo-android-debug", workflow)
        self.assertIn('release/Lubo-$RELEASE_TAG-windows.zip', workflow)
        self.assertIn('release/Lubo-$RELEASE_TAG-linux.zip', workflow)
        self.assertIn('release/Lubo-$RELEASE_TAG-android-debug.apk', workflow)
        self.assertIn('--title "Lubo $RELEASE_TAG"', workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)

    def test_android_app_and_entrypoint_use_lubo_class_and_title(self):
        self.assertIn("class LuboAndroidApp(App):", self.android_app)
        self.assertIn('title = "Lubo"', self.android_app)
        self.assertIn('text="Lubo"', self.android_app)
        self.assertIn("LuboAndroidApp().run()", self.android_app)
        self.assertIn(
            "from lubo.apps.android.main import LuboAndroidApp",
            self.android_entrypoint,
        )
        self.assertIn("LuboAndroidApp().run()", self.android_entrypoint)

    def test_product_build_and_app_files_contain_no_legacy_branding(self):
        legacy_markers = (
            "douyinlive" + "recorder",
            "douyin live " + "recorder",
            "douyin-live-" + "recorder",
            "org." + "douyinrecorder",
        )
        paths = [
            REPO_ROOT / "lubo" / "apps" / "desktop" / "main.py",
            REPO_ROOT / "lubo" / "apps" / "android" / "main.py",
            REPO_ROOT / "lubo" / "apps" / "android" / "platform.py",
            REPO_ROOT / "scripts" / "build_windows.ps1",
            REPO_ROOT / "scripts" / "build_linux.sh",
            REPO_ROOT / "scripts" / "build_android.sh",
            REPO_ROOT / ".github" / "workflows" / "build-desktop.yml",
            REPO_ROOT / ".github" / "workflows" / "build-android.yml",
            REPO_ROOT / ".github" / "workflows" / "publish-release.yml",
        ]
        paths.extend(
            path
            for path in (REPO_ROOT / "android").rglob("*")
            if path.is_file()
            and path.suffix in {".java", ".py", ".spec", ".xml"}
        )

        for path in paths:
            content = path.read_text(encoding="utf-8").casefold()
            for marker in legacy_markers:
                self.assertNotIn(marker, content, f"{marker!r} remains in {path}")
            compact_content = re.sub(r"[^a-z]+", "", content)
            self.assertNotIn(
                "douyinlive" + "recorderdesktopapp",
                compact_content,
                f"legacy desktop class compatibility remains in {path}",
            )

    def test_all_tracked_text_has_independent_project_identity(self):
        violations = []
        scanned = 0

        for relative_path, content in tracked_text_files():
            scanned += 1
            violations.extend(
                f"{relative_path}: {label}"
                for label in identity_violations(relative_path, content)
            )

        self.assertGreater(scanned, 50)
        self.assertEqual(
            violations,
            [],
            "Forbidden project identity remains:\n" + "\n".join(violations),
        )

    def test_tracked_text_decoder_rejects_nul_and_non_utf8_content(self):
        cases = (
            ("nul.txt", b"Lubo\0text", "NUL"),
            ("invalid.txt", b"\xff\xfeLubo", "UTF-8"),
            ("utf16.txt", "Lubo".encode("utf-16"), "NUL"),
        )

        for relative_path, content, expected_error in cases:
            with self.subTest(relative_path=relative_path):
                with self.assertRaisesRegex(ValueError, expected_error):
                    decode_tracked_text(relative_path, content)

    def test_identity_scan_matches_forbidden_tokens_case_insensitively(self):
        mixed_case_name = "dOuYiNlIvE" + "rEcOrDeR"

        self.assertIn(
            "old CamelCase application name",
            identity_violations("mixed.txt", mixed_case_name),
        )

    def test_notice_allows_only_one_exact_historical_author_name(self):
        self.assertNotIn(
            "old author",
            identity_violations(HISTORICAL_NOTICE_PATH, HISTORICAL_AUTHOR),
        )
        for content in (
            HISTORICAL_AUTHOR + "\n" + HISTORICAL_AUTHOR,
            HISTORICAL_AUTHOR + "\n" + "hM" + "IlY",
        ):
            with self.subTest(content=content):
                self.assertIn(
                    "old author",
                    identity_violations(HISTORICAL_NOTICE_PATH, content),
                )

    def test_notice_does_not_exempt_other_forbidden_tokens(self):
        mixed_case_name = "dOuYiNlIvE" + "rEcOrDeR"
        content = HISTORICAL_AUTHOR + "\n" + mixed_case_name

        self.assertIn(
            "old CamelCase application name",
            identity_violations(HISTORICAL_NOTICE_PATH, content),
        )

    def test_readme_is_complete_multi_platform_user_documentation(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        required_sections = (
            "# Lubo",
            "## 支持平台",
            "## Windows / Linux 桌面使用",
            "## Android 使用",
            "## URL_config.ini",
            "## 本地 config.ini",
            "## 源码安装",
            "## 构建",
            "## GitHub Releases",
            "## 架构",
            "## 测试",
            "## 法律与合规",
        )
        for section in required_sections:
            self.assertIn(section, readme)

        for platform in (
            "抖音 / Douyin",
            "Bilibili Live / B站直播",
            "Huya / 虎牙",
            "Douyu / 斗鱼",
        ):
            self.assertIn(platform, readme)

        for cookie_key in ("douyin", "bilibili", "huya", "douyu"):
            self.assertIn(f"`{cookie_key}`", readme)
        for section_name in ("recorder", "monitor", "proxy", "cookies"):
            self.assertIn(f"`[{section_name}]`", readme)

        self.assertIn("FLV/HTTP", readme)
        self.assertIn("HLS-only", readme)
        self.assertIn("不创建文件", readme)
        self.assertIn("本身不处理 HLS", readme)
        self.assertIn("requirements-gui.txt", readme)
        self.assertIn("python -m lubo.apps.desktop.main", readme)
        self.assertIn("dist/Lubo", readme)
        self.assertIn("dist/android/Lubo-android-debug.apk", readme)
        self.assertIn(CURRENT_REPOSITORY + "/releases", readme)
        self.assertIn("平台变更", readme)
        self.assertIn("风控", readme)
        self.assertIn("Cookie", readme)

        github_urls = re.findall(r"https://github\.com/[^\s)>]+", readme)
        self.assertTrue(github_urls)
        self.assertTrue(
            all(url.startswith(CURRENT_REPOSITORY) for url in github_urls),
            github_urls,
        )

    def test_pyproject_declares_exact_distribution_metadata(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as metadata_file:
            metadata = tomllib.load(metadata_file)

        project = metadata["project"]
        self.assertEqual(project["name"], "lubo-live-recorder")
        self.assertEqual(project["version"], "0.2.0a1")
        self.assertIn("独立", project["description"])
        self.assertIn("多平台", project["description"])
        self.assertEqual(project["authors"], [{"name": "zhangzhimiao1994"}])
        self.assertEqual(project["license"], {"text": "MIT"})
        self.assertEqual(project["requires-python"], ">=3.10,<3.14")
        candidate_versions = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")
        self.assertEqual(
            versions_matching_specifier(project["requires-python"], candidate_versions),
            ["3.10", "3.11", "3.12", "3.13"],
        )
        self.assertEqual(
            project["dependencies"],
            ["streamlink==8.4.0", "yt-dlp==2026.6.9"],
        )
        self.assertEqual(
            project["optional-dependencies"]["gui"],
            ["kivy>=2.3.0", "pyinstaller>=6.0.0"],
        )
        self.assertEqual(
            project["urls"],
            {
                "Homepage": CURRENT_REPOSITORY,
                "Documentation": CURRENT_REPOSITORY + "#readme",
                "Repository": CURRENT_REPOSITORY + ".git",
                "Issues": CURRENT_REPOSITORY + "/issues",
            },
        )

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.10–3.13", readme)
        for script in (self.windows_script, self.linux_script):
            self.assertIn("Python 3.10-3.13", script)

    def test_current_and_historical_mit_notices_are_complete(self):
        license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        notice = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            license_text,
            mit_license("Copyright (c) 2026 zhangzhimiao1994"),
        )
        self.assertEqual(
            notice,
            "# Historical MIT notice retained for license compliance\n\n"
            + mit_license("Copyright (c) 2025 " + HISTORICAL_AUTHOR),
        )

    def test_platform_documentation_matches_registered_adapters(self):
        platforms = (REPO_ROOT / "docs" / "platforms.md").read_text(
            encoding="utf-8"
        )
        expected_rows = (
            "| `douyin` | Douyin / 抖音 | `live.douyin.com`, `v.douyin.com`, `www.douyin.com` | 内置网页解析 | `douyin` |",
            "| `bilibili` | Bilibili Live / B站直播 | `live.bilibili.com` | Streamlink | `bilibili` |",
            "| `huya` | Huya / 虎牙 | `huya.com`, `www.huya.com`, `m.huya.com` | Streamlink | `huya` |",
            "| `douyu` | Douyu / 斗鱼 | `douyu.com`, `www.douyu.com`, `m.douyu.com` | 斗鱼公开 H5 API | `douyu` |",
        )
        for row in expected_rows:
            self.assertIn(row, platforms)
        self.assertEqual(platforms.count("仅直接 FLV/HTTP；HLS-only 报错且不创建文件"), 4)

    def test_only_current_issue_forms_and_pr_template_remain(self):
        issue_template_dir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
        self.assertEqual(
            sorted(path.name for path in issue_template_dir.iterdir()),
            ["bug.yml", "feature.yml"],
        )
        issue_url = CURRENT_REPOSITORY + "/issues"
        for template_name in ("bug.yml", "feature.yml"):
            content = (issue_template_dir / template_name).read_text(encoding="utf-8")
            self.assertIn("Lubo", content)
            self.assertIn(issue_url, content)

        pull_request_template = (
            REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
        ).read_text(encoding="utf-8")
        for heading in ("## 变更", "## 验证", "## 平台影响", "## 清单"):
            self.assertIn(heading, pull_request_template)

    def test_migration_documents_are_replaced_by_platform_reference(self):
        for relative_path in REMOVED_DOCUMENTS:
            self.assertFalse((REPO_ROOT / relative_path).exists(), relative_path)
        self.assertTrue((REPO_ROOT / "docs" / "platforms.md").is_file())

    def test_gitignore_keeps_templates_tracked_and_ignores_local_state(self):
        required_rules = (
            "__pycache__/",
            ".venv/",
            ".pytest_cache/",
            ".coverage",
            "build/",
            "dist/",
            "*.egg-info/",
            ".build-venv/",
            ".android-build/",
            ".buildozer/",
            "/config/config.local.ini",
            "/config/URL_config.local.ini",
            "recordings/",
            "downloads/",
            "logs/",
        )
        for rule in required_rules:
            self.assertIn(rule, self.gitignore_lines)

        for template_path in ("config/config.ini", "config/URL_config.ini"):
            ignored = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", template_path],
                cwd=REPO_ROOT,
                check=False,
            )
            self.assertEqual(ignored.returncode, 1, template_path)

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
