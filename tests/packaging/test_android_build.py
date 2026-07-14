import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class AndroidBuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = (REPO_ROOT / "android" / "buildozer.spec").read_text(encoding="utf-8")
        cls.script = (REPO_ROOT / "scripts" / "build_android.sh").read_text(encoding="utf-8")
        cls.manifest = (REPO_ROOT / "android" / "manifest" / "application.xml").read_text(encoding="utf-8")
        cls.workflow = (REPO_ROOT / ".github" / "workflows" / "build-android.yml").read_text(encoding="utf-8")

    def test_foreground_service_and_required_permissions_are_declared(self):
        self.assertIn(":foreground:sticky:foregroundServiceType=specialUse", self.spec)
        self.assertIn("FOREGROUND_SERVICE_SPECIAL_USE", self.spec)
        self.assertIn("POST_NOTIFICATIONS", self.spec)
        self.assertIn("PROPERTY_SPECIAL_USE_FGS_SUBTYPE", self.manifest)
        self.assertIn("StopRecorderReceiver", self.manifest)

    def test_build_stages_shared_code_and_sanitized_config(self):
        self.assertIn('cp -R -- "$REPO_ROOT/douyinliverecorder"', self.script)
        self.assertIn('cp -R -- "$REPO_ROOT/src"', self.script)
        self.assertIn("prepare_packaged_config.py", self.script)
        self.assertIn("DouyinLiveRecorder-android-debug.apk", self.script)
        self.assertIn(".android-build/project/appsource", self.script)

    def test_ci_builds_and_uploads_apk(self):
        self.assertIn("scripts/build_android.sh", self.workflow)
        self.assertIn("set -o pipefail", self.workflow)
        self.assertIn("actions/upload-artifact@v4", self.workflow)
        self.assertIn("dist/android/DouyinLiveRecorder-android-debug.apk", self.workflow)

    def test_android_entrypoints_and_java_sources_exist(self):
        required = [
            "android/main.py",
            "android/service/recorder_service.py",
            "android/java/org/douyinrecorder/mobile/RecorderPythonService.java",
            "android/java/org/douyinrecorder/mobile/StopRecorderReceiver.java",
        ]
        self.assertTrue(all((REPO_ROOT / path).is_file() for path in required))


if __name__ == "__main__":
    unittest.main()
