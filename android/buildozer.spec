[app]
title = Lubo
package.name = recorder
package.domain = org.lubo
source.dir = appsource
source.include_exts = py,ini,json,js,java,xml,png,jpg
source.exclude_dirs = tests,build,dist,.git,.github
version = 0.2.0a3
android.numeric_version = 20003
requirements = python3,kivy==2.3.1,pyjnius,android,streamlink==8.4.0,yt-dlp==2026.6.9
orientation = portrait
fullscreen = 0
services = recorder:service/recorder_service.py:foreground:sticky:foregroundServiceType=specialUse

android.permissions = INTERNET,WAKE_LOCK,POST_NOTIFICATIONS,FOREGROUND_SERVICE,FOREGROUND_SERVICE_SPECIAL_USE
android.api = 36
android.minapi = 26
android.ndk = 29
android.archs = arm64-v8a,armeabi-v7a
android.accept_sdk_license = True
android.add_src = %(source.dir)s/java
android.service_class_name = org.lubo.recorder.RecorderPythonService
p4a.branch = develop
p4a.hook = p4a_hook.py

[buildozer]
log_level = 2
warn_on_root = 1
