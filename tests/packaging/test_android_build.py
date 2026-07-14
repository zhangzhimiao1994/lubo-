import ast
import configparser
import runpy
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lubo.apps.android.platform import STOP_REQUEST_FILE


REPO_ROOT = Path(__file__).resolve().parents[2]


class AndroidBuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = (REPO_ROOT / "android" / "buildozer.spec").read_text(encoding="utf-8")
        cls.entrypoint = (REPO_ROOT / "android" / "main.py").read_text(encoding="utf-8")
        cls.script = (REPO_ROOT / "scripts" / "build_android.sh").read_text(encoding="utf-8")
        cls.hook_path = REPO_ROOT / "android" / "p4a_hook.py"
        cls.hook = cls.hook_path.read_text(encoding="utf-8")
        cls.receiver = (
            REPO_ROOT
            / "android"
            / "java"
            / "org"
            / "lubo"
            / "recorder"
            / "StopRecorderReceiver.java"
        ).read_text(encoding="utf-8")
        cls.workflow = (REPO_ROOT / ".github" / "workflows" / "build-android.yml").read_text(encoding="utf-8")

    def test_buildozer_uses_lubo_identity_and_pinned_runtime(self):
        parser = configparser.RawConfigParser()
        parser.read_string(self.spec)
        app = parser["app"]

        self.assertEqual(app["title"], "Lubo")
        self.assertEqual(app["package.name"], "recorder")
        self.assertEqual(app["package.domain"], "org.lubo")
        self.assertEqual(
            app["android.service_class_name"],
            "org.lubo.recorder.RecorderPythonService",
        )
        self.assertEqual(app["version"], "0.2.0a1")
        self.assertEqual(
            app["requirements"],
            "python3,kivy==2.3.1,pyjnius,android,streamlink==8.4.0,yt-dlp==2026.6.9",
        )

    def test_distribution_buildozer_and_android_entrypoint_versions_match(self):
        with (REPO_ROOT / "pyproject.toml").open("rb") as metadata_file:
            distribution_version = tomllib.load(metadata_file)["project"]["version"]

        parser = configparser.RawConfigParser()
        parser.read_string(self.spec)
        buildozer_version = parser["app"]["version"]
        entrypoint_tree = ast.parse(self.entrypoint)
        entrypoint_version = next(
            node.value.value
            for node in entrypoint_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )

        self.assertEqual(distribution_version, "0.2.0a1")
        self.assertEqual(buildozer_version, distribution_version)
        self.assertEqual(entrypoint_version, distribution_version)

    def test_prerelease_display_version_has_explicit_android_numeric_version(self):
        parser = configparser.RawConfigParser()
        parser.read_string(self.spec)
        app = parser["app"]
        display_version = app["version"]

        self.assertRegex(display_version, r"[a-zA-Z]")
        self.assertIn(
            "android.numeric_version",
            app,
            "p4a must not derive versionCode from the prerelease display version",
        )
        numeric_version_text = app["android.numeric_version"]
        self.assertRegex(numeric_version_text, r"^[1-9][0-9]*$")
        numeric_version = int(numeric_version_text)
        self.assertEqual(numeric_version, 20001)
        self.assertGreaterEqual(numeric_version, 1)
        self.assertLessEqual(numeric_version, 2_100_000_000)
        self.assertNotEqual(numeric_version_text, display_version)

    def test_foreground_service_and_required_permissions_are_declared(self):
        self.assertIn(":foreground:sticky:foregroundServiceType=specialUse", self.spec)
        self.assertIn("FOREGROUND_SERVICE_SPECIAL_USE", self.spec)
        self.assertIn("POST_NOTIFICATIONS", self.spec)
        self.assertIn("p4a.hook = p4a_hook.py", self.spec)
        self.assertIn("PROPERTY_SPECIAL_USE_FGS_SUBTYPE", self.hook)
        self.assertIn("StopRecorderReceiver", self.hook)

    def test_build_stages_current_code_and_sanitized_config_without_legacy_src(self):
        self.assertIn('cp -R -- "$REPO_ROOT/lubo"', self.script)
        self.assertNotIn('cp -R -- "$REPO_ROOT/src"', self.script)
        self.assertNotIn('"$SOURCE_DIR/src"', self.script)
        self.assertIn('cp -- "$REPO_ROOT/android/main.py"', self.script)
        self.assertIn('cp -- "$REPO_ROOT/android/service/recorder_service.py"', self.script)
        self.assertIn('cp -R -- "$REPO_ROOT/android/java"', self.script)
        self.assertIn("prepare_packaged_config.py", self.script)
        self.assertIn('cp -- "$REPO_ROOT/android/p4a_hook.py"', self.script)
        self.assertIn("Lubo-android-debug.apk", self.script)
        self.assertIn("dist/android/Lubo-android-debug.apk", self.script)
        self.assertIn(".android-build/project/appsource", self.script)

    def test_java_stop_marker_matches_python_storage_contract(self):
        expected = f'new File(context.getFilesDir(), "{STOP_REQUEST_FILE}")'

        self.assertIn(expected, self.receiver)
        self.assertNotIn('"app/stop.request"', self.receiver)
        self.assertNotIn("getParentFile()", self.receiver)
        self.assertNotIn("mkdirs()", self.receiver)

    def test_android_build_rebuilds_verified_bin_before_buildozer(self):
        self.assertIn('BIN_DIR="$PROJECT_DIR/bin"', self.script)
        self.assertIn(
            'EXPECTED_BIN_DIR="$REPO_ROOT/.android-build/project/bin"',
            self.script,
        )
        self.assertIn('"$BIN_DIR" != "$EXPECTED_BIN_DIR"', self.script)
        self.assertIn('-L "$BIN_DIR"', self.script)

        cleanup = self.script.index('rm -rf -- "$BIN_DIR"')
        recreate = self.script.index('mkdir -p "$BIN_DIR"')
        build = self.script.index('"$BUILDOZER_BIN" android debug')
        self.assertLess(cleanup, recreate)
        self.assertLess(recreate, build)

    def test_android_build_requires_exactly_one_new_apk(self):
        self.assertIn("mapfile -d '' APK_PATHS", self.script)
        self.assertRegex(self.script, r'\$\{#APK_PATHS\[@\]\}\s+-ne\s+1')
        self.assertIn('APK_PATH="${APK_PATHS[0]}"', self.script)
        self.assertNotIn("-print -quit", self.script)

    def test_p4a_hook_inserts_manifest_children_once(self):
        patch_manifest_template = runpy.run_path(str(self.hook_path))[
            "patch_manifest_template"
        ]
        template = """<application>
        {% for name, foreground_type in service_data %}
        <service android:name="old" />
        {% endfor %}
</application>
"""
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "AndroidManifest.tmpl.xml"
            path.write_text(template, encoding="utf-8")

            patch_manifest_template(path)
            first = path.read_text(encoding="utf-8")
            patch_manifest_template(path)

            self.assertEqual(path.read_text(encoding="utf-8"), first)
            self.assertEqual(first.count("lubo-special-use-service"), 1)
            self.assertEqual(first.count("StopRecorderReceiver"), 1)
            self.assertIn("PROPERTY_SPECIAL_USE_FGS_SUBTYPE", first)
            self.assertIn("org.lubo.recorder.StopRecorderReceiver", first)

    def test_ci_builds_and_uploads_apk(self):
        self.assertIn("scripts/build_android.sh", self.workflow)
        self.assertIn("set -o pipefail", self.workflow)
        self.assertIn("title=Android build log", self.workflow)
        self.assertIn("PYTHONUSERBASE", self.workflow)
        self.assertIn('.android-user/bin" >> "$GITHUB_PATH', self.workflow)
        self.assertIn("actions/cache/save@v4", self.workflow)
        self.assertIn("actions/upload-artifact@v4", self.workflow)
        self.assertIn("name: Lubo-android-debug", self.workflow)
        self.assertIn("dist/android/Lubo-android-debug.apk", self.workflow)

    def test_android_entrypoints_and_java_sources_exist(self):
        required = [
            "android/main.py",
            "android/p4a_hook.py",
            "android/service/recorder_service.py",
            "android/java/org/lubo/recorder/RecorderPythonService.java",
            "android/java/org/lubo/recorder/StopRecorderReceiver.java",
        ]
        self.assertTrue(all((REPO_ROOT / path).is_file() for path in required))
        self.assertFalse((REPO_ROOT / "android/java/org/douyinrecorder").exists())

        for path in required[-2:]:
            source = (REPO_ROOT / path).read_text(encoding="utf-8")
            self.assertIn("package org.lubo.recorder;", source)


if __name__ == "__main__":
    unittest.main()
