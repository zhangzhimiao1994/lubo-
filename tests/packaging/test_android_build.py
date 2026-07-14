import runpy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[2]


class AndroidBuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = (REPO_ROOT / "android" / "buildozer.spec").read_text(encoding="utf-8")
        cls.script = (REPO_ROOT / "scripts" / "build_android.sh").read_text(encoding="utf-8")
        cls.hook_path = REPO_ROOT / "android" / "p4a_hook.py"
        cls.hook = cls.hook_path.read_text(encoding="utf-8")
        cls.workflow = (REPO_ROOT / ".github" / "workflows" / "build-android.yml").read_text(encoding="utf-8")

    def test_foreground_service_and_required_permissions_are_declared(self):
        self.assertIn(":foreground:sticky:foregroundServiceType=specialUse", self.spec)
        self.assertIn("FOREGROUND_SERVICE_SPECIAL_USE", self.spec)
        self.assertIn("POST_NOTIFICATIONS", self.spec)
        self.assertIn("p4a.hook = p4a_hook.py", self.spec)
        self.assertIn("PROPERTY_SPECIAL_USE_FGS_SUBTYPE", self.hook)
        self.assertIn("StopRecorderReceiver", self.hook)

    def test_build_stages_shared_code_and_sanitized_config(self):
        self.assertIn('cp -R -- "$REPO_ROOT/douyinliverecorder"', self.script)
        self.assertIn('cp -R -- "$REPO_ROOT/src"', self.script)
        self.assertIn("prepare_packaged_config.py", self.script)
        self.assertIn('cp -- "$REPO_ROOT/android/p4a_hook.py"', self.script)
        self.assertIn("DouyinLiveRecorder-android-debug.apk", self.script)
        self.assertIn(".android-build/project/appsource", self.script)

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

    def test_ci_builds_and_uploads_apk(self):
        self.assertIn("scripts/build_android.sh", self.workflow)
        self.assertIn("set -o pipefail", self.workflow)
        self.assertIn("title=Android build log", self.workflow)
        self.assertIn("PYTHONUSERBASE", self.workflow)
        self.assertIn('.android-user/bin" >> "$GITHUB_PATH', self.workflow)
        self.assertIn("actions/cache/save@v4", self.workflow)
        self.assertIn("actions/upload-artifact@v4", self.workflow)
        self.assertIn("dist/android/DouyinLiveRecorder-android-debug.apk", self.workflow)

    def test_android_entrypoints_and_java_sources_exist(self):
        required = [
            "android/main.py",
            "android/p4a_hook.py",
            "android/service/recorder_service.py",
            "android/java/org/douyinrecorder/mobile/RecorderPythonService.java",
            "android/java/org/douyinrecorder/mobile/StopRecorderReceiver.java",
        ]
        self.assertTrue(all((REPO_ROOT / path).is_file() for path in required))


if __name__ == "__main__":
    unittest.main()
