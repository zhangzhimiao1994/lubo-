# Cross-platform applications

The refactored application currently provides a Douyin-first recording path for
Windows, Linux, and Android. The legacy `main.py` remains available for platforms
that have not moved to the new adapter layer yet.

## Windows

Requirements: Python 3.10 through 3.13 and FFmpeg on `PATH`. The build embeds the
selected FFmpeg executable.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Run `dist/DouyinLiveRecorder/DouyinLiveRecorder.exe` without separating it from
the accompanying `_internal` directory.

## Linux

Install Python 3.10 through 3.13, FFmpeg, and the Kivy system dependencies for the
selected distribution, then run:

```bash
chmod +x scripts/build_linux.sh
scripts/build_linux.sh
```

Linux artifacts must be built on the target distribution or a compatible image.

## Android

The Android app records on the phone. Monitoring runs in a sticky foreground
service and displays a persistent notification with a **Stop recording** action.
Recordings are written under the application's private `recordings` directory.

The current Android vertical slice directly saves Douyin FLV streams. It does not
transcode on the phone, and rejects HLS-only streams instead of saving a playlist
as a video file.

Buildozer and python-for-android require Linux:

```bash
chmod +x scripts/build_android.sh
scripts/build_android.sh
```

The APK is copied to `dist/android/DouyinLiveRecorder-android-debug.apk`. The
`Build Android APK` GitHub Actions workflow performs the same build and uploads
the APK as an artifact.

Android 13 and newer ask for notification permission. Android 14 and newer use
the `specialUse` foreground-service type for user-started continuous live-room
monitoring; Play Store publication requires a matching declaration in Play
Console.

## Configuration

Desktop and Android copy a sanitized default configuration into their writable
application-data directory on first launch. Add a Douyin cookie to the local
configuration only when needed. Packaging scripts remove cookies, account
credentials, save paths, and default target URLs from release artifacts.
