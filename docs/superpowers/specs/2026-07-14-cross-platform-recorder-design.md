# Cross-Platform Live Recorder Refactor Design

## Goal

Refactor DouyinLiveRecorder from a script-centered recorder into a shared recording core with graphical Windows, Linux, and Android applications.

The first release targets GUI-based operation on all three platforms. Android must record locally on the phone, support long-running watch mode, and use a foreground service with a persistent notification while monitoring or recording.

## MVP Scope

The first migrated platform set is:

- Douyin
- TikTok
- Kuaishou
- Bilibili
- Huya
- Douyu
- YouTube

Douyin is the highest-priority platform for the first working vertical slice. Existing platforms outside this list remain available through the legacy `main.py` path until they are migrated.

## Non-Goals

- Migrating all current 50+ platforms in the first release.
- Removing `main.py` immediately.
- Replacing FFmpeg with a new recorder engine.
- Building a remote server product. The Android app records on-device.
- Hiding Android background recording without a notification. Stability takes priority.

## Current-State Findings

The current repository already has useful reusable layers:

- `src/spider.py` contains platform-specific live data fetching.
- `src/stream.py` turns fetched data into stream URLs and quality choices.
- `src/http_clients/` wraps sync and async HTTP calls.
- `msg_push.py` contains push-channel integrations.
- `ffmpeg_install.py` handles desktop FFmpeg discovery and installation.

The main problem is that `main.py` owns too many responsibilities:

- global runtime state
- config loading and mutation
- URL file parsing
- platform dispatch
- FFmpeg command construction
- thread lifecycle
- retry timing
- display output
- push notification triggering
- conversion and post-processing

This makes GUI integration risky because the UI would otherwise need to drive global variables and long-running loops directly.

## Recommended Architecture

Use a Python-first architecture:

- shared Python recording core
- Kivy GUI for Windows and Linux
- Kivy Android APK built with Buildozer/python-for-android
- Android foreground service bridge for long-running watch and recording

This approach reuses the existing Python platform parsing and recording logic while still providing native packages for each target.

## Package Layout

Add new modules without deleting the legacy entry point:

```text
douyinliverecorder/
  core/
    config.py
    models.py
    events.py
    scheduler.py
    task_manager.py
    url_store.py
    state_store.py
  platforms/
    base.py
    registry.py
    douyin.py
    tiktok.py
    kuaishou.py
    bilibili.py
    huya.py
    douyu.py
    youtube.py
  recorders/
    ffmpeg.py
    postprocess.py
    subtitles.py
    paths.py
  notifications/
    dispatcher.py
    desktop.py
    android.py
    push_channels.py
  apps/
    desktop/
      main.py
      views/
      widgets/
    android/
      main.py
      service.py
      permissions.py
  cli/
    main.py
```

Existing `src/` modules can be imported during the first migration, then gradually moved or wrapped behind platform adapters.

## Core Concepts

`RecordingTarget`

- stable ID
- original URL
- optional display name
- desired quality
- enabled/paused state
- platform key after detection

`StreamInfo`

- platform key and platform display name
- anchor name
- title
- live status
- selected quality
- primary recording URL
- optional FLV URL
- optional HLS URL
- request headers

`RecordingTask`

- target ID
- process status
- output file path
- start time
- current duration
- last error
- retry counters
- FFmpeg process handle

`RecorderEvent`

- target added/updated/removed
- live detected
- recording started/stopped/failed
- retry scheduled
- disk-space warning
- conversion started/completed
- notification sent/failed

The GUI subscribes to events and renders state. It does not parse log text to infer state.

## Platform Adapter Interface

Each migrated platform implements one small contract:

```python
class PlatformAdapter:
    key: str
    display_name: str

    def matches(self, url: str) -> bool:
        ...

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        ...
```

`ResolveContext` provides proxy settings, cookies, account credentials, preferred quality, and HTTP helpers. This prevents adapters from reading global config directly.

The registry checks adapters in priority order and returns the first match. Unknown URLs that directly point to `.flv` or `.m3u8` use a generic direct-stream adapter.

## Scheduler Design

The scheduler owns watch mode:

- load enabled targets from the URL store
- detect added, removed, paused, and edited targets
- run platform resolves with bounded concurrency
- start FFmpeg only when `StreamInfo.is_live` is true
- keep one recording process per target
- back off after repeated platform or network failures
- emit events for the GUI and notification layer

The first implementation can keep threads around FFmpeg processes, but platform resolving should move toward `asyncio` so the GUI remains responsive and concurrency is easier to limit.

## Recording Design

`recorders.ffmpeg.FFmpegRecorder` builds and runs FFmpeg commands. It supports:

- TS, MP4, MKV, FLV, MP3, and M4A outputs where platform-compatible
- segmented recording
- direct stream download fallback where FFmpeg is not needed or not available
- stop by target ID
- output naming by anchor, title, and timestamp
- completion hooks for conversion, subtitle generation, and custom scripts

Platform-specific FFmpeg paths are injected:

- Windows package bundles `ffmpeg.exe`
- Linux package requires system FFmpeg or bundles one for AppImage-style builds
- Android package uses an Android-compatible FFmpeg binary or ffmpeg-kit integration

## Configuration Design

Keep compatibility with existing `config/config.ini` and `config/URL_config.ini` for the first release.

Add typed load/save wrappers so the rest of the app uses Python models instead of raw Chinese INI keys. The wrapper must preserve unknown keys and comments as much as possible because many users edit the files manually.

The desktop and Android GUI write through `ConfigService` and `UrlStore`, not directly through `configparser`.

## GUI Design

Windows and Linux share the Kivy desktop app. Android uses the same main screens with mobile-specific navigation and service controls.

Required first-release screens:

- Targets: add, edit, pause, delete, reorder, and bulk enable targets.
- Monitor: current live status, recording state, duration, quality, output file, and last error.
- Settings: save path, quality, format, loop interval, proxy, concurrency, segmentation, conversion, cookies, and push settings.
- Logs: searchable recent runtime events and errors.

The UI must expose the common workflow directly on the first screen: add a live room URL, start monitoring, see whether it is recording, and stop it.

## Android Design

Android records locally on the phone.

Runtime behavior:

- monitoring and recording run inside a foreground service
- the service shows a persistent notification while active
- notification displays active recording count and the most important current status
- users can stop monitoring from the notification
- the app requests network and notification permissions
- storage is handled through app-specific storage first, with export/share actions for recorded files

Battery and process survival:

- foreground service is mandatory during monitoring or recording
- the app should show a settings shortcut or instruction screen for battery optimization exemptions when the device vendor aggressively kills background work
- the scheduler persists target and task state so the service can recover after process restart

Android constraints:

- bundled FFmpeg must be Android-compatible
- file paths must avoid desktop-only assumptions
- scripts and desktop-only post-processing are disabled on Android unless explicitly supported later
- very high concurrency is discouraged on phones

## Packaging Design

Windows:

- PyInstaller builds a GUI executable
- package includes default `config/`, `i18n/`, JS signing files, and FFmpeg
- output directory package is preferred first; single-file packaging can follow after stability checks

Linux:

- package a runnable directory or AppImage-style artifact
- document system FFmpeg requirement if not bundled
- keep Docker/headless mode available for existing server users

Android:

- Buildozer/python-for-android builds APK
- include required Python dependencies and Java/Kotlin service bridge
- include foreground service declaration and permissions in the Android manifest
- include Android FFmpeg integration

## Migration Strategy

Use incremental migration to avoid breaking existing users.

Phase 1:

- create core models, config wrapper, event bus, and URL store
- add adapter interface and migrate Douyin
- add FFmpeg runner wrapper
- add a small desktop GUI vertical slice: add URL, start monitor, record Douyin, stop

Phase 2:

- migrate TikTok, Kuaishou, Bilibili, Huya, Douyu, and YouTube
- add full settings screens and task list actions
- add post-processing and push-channel wrappers

Phase 3:

- add Android service and local recording
- integrate Android FFmpeg
- validate foreground service lifecycle and storage flows

Phase 4:

- add Windows, Linux, and Android packaging scripts
- create release artifacts
- document how legacy config maps into the new GUI

Phase 5:

- migrate remaining platforms in batches
- retire or thin down legacy `main.py` after parity is proven

## Testing Strategy

Unit tests:

- config defaulting and round-trip preservation
- URL parsing and duplicate handling
- platform registry matching
- quality selection
- FFmpeg command construction
- output path sanitization
- retry and scheduler state transitions

Integration tests:

- adapter resolves with mocked HTTP responses for each MVP platform
- scheduler starts and stops fake recorder processes
- config edits from GUI services update files correctly

Manual verification:

- Windows GUI records a Douyin live room
- Linux GUI records a Douyin live room
- Android APK monitors in foreground service and records to phone storage
- stopping from GUI and Android notification closes FFmpeg cleanly

## Risks

Android FFmpeg packaging is the highest technical risk. It should be proven with a small vertical slice before broad UI work.

Some platform parsers depend on JS execution or Node availability. Android packaging must verify whether those paths run under python-for-android or need replacement.

Long-running Android recording depends on vendor background restrictions. Foreground service improves reliability but cannot override every device policy.

The existing config uses localized keys and manual comments. The wrapper must avoid destructive rewrites.

## Acceptance Criteria

- A user can install and run the Windows GUI without using the terminal.
- A user can install and run the Linux GUI without editing Python code.
- A user can install the Android APK, add a Douyin live URL, start monitoring, leave the app, and continue recording with a foreground notification.
- The MVP platforms are represented by platform adapters rather than `start_record()` URL branches.
- Existing `config.ini` and `URL_config.ini` remain usable.
- Legacy `main.py` still runs during the transition.
