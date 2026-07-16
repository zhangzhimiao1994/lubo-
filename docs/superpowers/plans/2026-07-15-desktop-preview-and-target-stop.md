# Desktop Preview and Per-Target Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one embedded muted desktop live preview and a race-free per-target stop-and-pause workflow without changing Android behavior.

**Architecture:** Keep decoding and lifecycle logic in desktop-only services with no Kivy dependency, then render immutable RGBA frames in a focused Kivy widget. Extend the shared scheduler with explicit target suppression so a target paused during resolution cannot start recording afterward. PyAV handles FLV/HLS/HTTP decoding while the existing FFmpeg recorder remains unchanged.

**Tech Stack:** Python 3.10-3.13, Kivy 2.3.x, PyAV 17.0.1, PyInstaller 6.x, unittest/pytest, FFmpeg, GitHub Actions

---

## File Structure

- Create `lubo/apps/desktop/preview.py`: preview states, immutable updates/frames, single-session lifecycle, retries, and stale-generation rejection.
- Create `lubo/apps/desktop/pyav_decoder.py`: lazy PyAV import, safe HTTP options, bounded timeouts, scaling, frame throttling, and container cleanup.
- Create `lubo/apps/desktop/preview_widget.py`: stable 16:9 Kivy texture pane and preview status controls.
- Modify `lubo/core/scheduler.py`: suppression set, pause/resume API, in-flight start guard, and preview stream resolution.
- Modify `lubo/apps/desktop/controller.py`: persist stop-and-pause/resume semantics and expose targets by ID.
- Modify `lubo/apps/desktop/main.py`: two-column monitoring layout, actionable target rows, preview callbacks, and shutdown order.
- Modify `requirements-gui.txt` and `pyproject.toml`: desktop-only pinned PyAV dependency.
- Modify `scripts/build_windows.ps1`, `scripts/build_linux.sh`, and packaging tests: collect and verify PyAV binaries.
- Create focused tests under `tests/apps/desktop/`; extend scheduler, controller, and packaging tests.

### Task 1: Commit the Verified Alpha.3 Stabilization Baseline

**Files:**
- Modify: `.github/workflows/publish-release.yml`
- Modify: `README.md`
- Modify: `android/buildozer.spec`
- Modify: `android/main.py`
- Modify: `config/config.ini`
- Modify: `lubo/apps/android/service.py`
- Modify: `lubo/apps/desktop/main.py`
- Modify: `lubo/core/config.py`
- Modify: `lubo/core/scheduler.py`
- Modify: `pyproject.toml`
- Modify: `scripts/prepare_packaged_config.py`
- Modify: `tests/apps/desktop/test_controller.py`
- Create: `tests/apps/desktop/test_logging.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/core/test_scheduler.py`
- Modify: `tests/packaging/test_android_build.py`
- Modify: `tests/packaging/test_build_scripts.py`

- [ ] **Step 1: Run the full baseline suite again**

Run:

```powershell
$env:KIVY_NO_FILELOG='1'
$env:KIVY_LOG_MODE='PYTHON'
.\.build-venv\windows\Scripts\python.exe -m pytest -q
```

Expected: `252 passed, 76 subtests passed`.

- [ ] **Step 2: Verify the pending baseline diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only the alpha.3 version, disk guard, desktop logging, Android output-directory fix, release metadata, and their tests are pending.

- [ ] **Step 3: Commit the stabilization baseline**

```powershell
git add .github/workflows/publish-release.yml README.md android/buildozer.spec android/main.py config/config.ini lubo/apps/android/service.py lubo/apps/desktop/main.py lubo/core/config.py lubo/core/scheduler.py pyproject.toml scripts/prepare_packaged_config.py tests/apps/desktop/test_controller.py tests/apps/desktop/test_logging.py tests/core/test_config.py tests/core/test_scheduler.py tests/packaging/test_android_build.py tests/packaging/test_build_scripts.py
git commit -m "feat: harden alpha.3 recording operations"
```

### Task 2: Add Race-Free Target Suppression and Controller Actions

**Files:**
- Modify: `lubo/core/scheduler.py`
- Modify: `lubo/apps/desktop/controller.py`
- Modify: `tests/core/test_scheduler.py`
- Modify: `tests/apps/desktop/test_controller.py`

- [ ] **Step 1: Write scheduler suppression tests**

Add tests that hold an adapter resolution open, pause the target, release the resolver, and assert no recorder process starts:

```python
async def test_pause_target_suppresses_inflight_start(self):
    entered = asyncio.Event()
    release = asyncio.Event()
    adapter = BlockingAdapter(entered=entered, release=release, is_live=True)
    scheduler, recorder, _events = self.make_scheduler(adapter=adapter)
    target = RecordingTarget("https://live.example/room")

    check = asyncio.create_task(scheduler.check_once([target]))
    await entered.wait()
    scheduler.pause_target(target.id)
    release.set()
    await check

    self.assertEqual(recorder.start_calls, [])
    self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.IDLE)

def test_resume_target_allows_later_check(self):
    scheduler, recorder, _events = self.make_scheduler()
    target = RecordingTarget("https://live.example/room")
    scheduler.pause_target(target.id)
    scheduler.resume_target(target.id)

    asyncio.run(scheduler.check_once([target]))

    self.assertEqual(len(recorder.start_calls), 1)
```

- [ ] **Step 2: Run the scheduler tests and verify failure**

Run:

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/core/test_scheduler.py -q
```

Expected: FAIL because `pause_target` and `resume_target` do not exist.

- [ ] **Step 3: Implement scheduler suppression and preview resolution**

Add a suppression set under the lifecycle lock and public methods:

```python
self._suppressed: set[str] = set()

def pause_target(self, target_id: str) -> None:
    with self._lifecycle_lock:
        self._suppressed.add(target_id)
    self.stop_target(target_id)

def resume_target(self, target_id: str) -> None:
    with self._lifecycle_lock:
        self._suppressed.discard(target_id)

def _is_suppressed(self, target_id: str) -> bool:
    with self._lifecycle_lock:
        return target_id in self._suppressed
```

Exclude suppressed IDs in the claim loop and recheck immediately before `recorder.start(command)`. When suppression wins the race, set the task to `IDLE` without publishing a recording-failed event.

Extract adapter resolution into `_resolve_target(target)` and add:

```python
async def resolve_preview_stream(self, target: RecordingTarget) -> StreamInfo:
    with self._lifecycle_lock:
        task = self._tasks.get(target.id)
        if (
            task is not None
            and task.status in {RecordingStatus.LIVE, RecordingStatus.RECORDING}
            and task.stream is not None
            and task.stream.is_live
        ):
            return task.stream
    return await self._resolve_target(target)
```

- [ ] **Step 4: Add controller persistence tests**

```python
def test_stop_and_pause_persists_before_stopping(self):
    target = RecordingTarget("https://live.douyin.com/111")
    controller, store, scheduler = self.make_controller([target])

    controller.stop_and_pause_target(target.id)

    self.assertFalse(controller.targets[0].enabled)
    self.assertFalse(store.saved_targets[0].enabled)
    scheduler.pause_target.assert_called_once_with(target.id)

def test_resume_target_persists_and_unsuppresses(self):
    target = RecordingTarget("https://live.douyin.com/111", enabled=False)
    controller, store, scheduler = self.make_controller([target])

    controller.resume_target(target.id)

    self.assertTrue(controller.targets[0].enabled)
    scheduler.resume_target.assert_called_once_with(target.id)
```

- [ ] **Step 5: Implement controller actions**

```python
def target_by_id(self, target_id: str) -> RecordingTarget | None:
    return next((item for item in self.targets if item.id == target_id), None)

def stop_and_pause_target(self, target_id: str) -> None:
    self.set_target_enabled(target_id, False)
    self.scheduler.pause_target(target_id)

def resume_target(self, target_id: str) -> None:
    self.set_target_enabled(target_id, True)
    self.scheduler.resume_target(target_id)
```

- [ ] **Step 6: Run focused tests and commit**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/core/test_scheduler.py tests/apps/desktop/test_controller.py -q
git add lubo/core/scheduler.py lubo/apps/desktop/controller.py tests/core/test_scheduler.py tests/apps/desktop/test_controller.py
git commit -m "feat: stop and pause individual targets"
```

Expected: focused tests pass.

### Task 3: Implement the PyAV Video Decoder

**Files:**
- Create: `lubo/apps/desktop/pyav_decoder.py`
- Create: `tests/apps/desktop/test_pyav_decoder.py`

- [ ] **Step 1: Write decoder contract tests with fake containers**

Cover safe header mapping, timeout values, video-only decode, 1280x720 bounding, RGBA row-stride compaction, 15 FPS throttling, and close:

```python
def test_decoder_opens_with_safe_headers_and_bounded_timeout(self):
    opened = []
    decoder = PyAvDecoder(open_container=lambda *a, **kw: opened.append((a, kw)) or FakeContainer())
    stream = StreamInfo(
        platform_key="huya",
        platform_name="Huya",
        is_live=True,
        primary_url="https://pull.example/live.flv?token=secret",
        headers={"User-Agent": "Lubo", "Referer": "https://www.huya.com/", "Cookie": "secret"},
    )

    list(decoder.frames(stream, Event()))

    _args, kwargs = opened[0]
    self.assertEqual(kwargs["timeout"], (10.0, 3.0))
    self.assertEqual(kwargs["options"]["user_agent"], "Lubo")
    self.assertIn("Referer: https://www.huya.com/", kwargs["options"]["headers"])
    self.assertNotIn("Cookie", kwargs["options"]["headers"])
```

- [ ] **Step 2: Run the decoder tests and verify failure**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_pyav_decoder.py -q
```

Expected: collection fails because `pyav_decoder.py` does not exist.

- [ ] **Step 3: Implement lazy PyAV decoding**

Define:

```python
@dataclass(frozen=True, slots=True)
class DecodedFrame:
    width: int
    height: int
    rgba: bytes

class PyAvDecoder:
    def __init__(self, open_container=None, monotonic=time.monotonic) -> None:
        self._open_container = open_container or self._open_with_pyav
        self._monotonic = monotonic
        self._lock = Lock()
        self._container = None

    @staticmethod
    def _open_with_pyav(url: str, **kwargs):
        import av
        return av.open(url, mode="r", **kwargs)
```

Map only `user-agent`, `referer`, and `origin` into FFmpeg options. Open with `timeout=(10.0, 3.0)`, choose the first video stream, skip frames delivered sooner than `1 / 15`, preserve aspect ratio within 1280x720, reformat to `rgba`, remove per-row padding, and yield `DecodedFrame`. `close()` atomically takes and closes the current container.

- [ ] **Step 4: Run tests and commit**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_pyav_decoder.py -q
git add lubo/apps/desktop/pyav_decoder.py tests/apps/desktop/test_pyav_decoder.py
git commit -m "feat: decode bounded desktop preview frames"
```

Expected: decoder tests pass without requiring PyAV to be installed because the tests inject `open_container`.

### Task 4: Implement Single-Session Preview Lifecycle

**Files:**
- Create: `lubo/apps/desktop/preview.py`
- Create: `tests/apps/desktop/test_preview.py`

- [ ] **Step 1: Write session state and generation tests**

```python
def test_switch_rejects_late_frames_from_previous_generation(self):
    first = ControlledDecoder()
    second = ControlledDecoder()
    session = PreviewSession(
        resolver=FakeResolver.live,
        decoder_factory=SequenceFactory(first, second),
        retry_delays=(),
    )
    received = []

    session.start(TARGET_A, received.append)
    session.start(TARGET_B, received.append)
    first.emit(FRAME_A)
    second.emit(FRAME_B)

    self.assertEqual([item.target_id for item in received if item.frame], [TARGET_B.id])

def test_network_failure_retries_three_times_then_fails(self):
    session = PreviewSession(
        resolver=FakeResolver.live,
        decoder_factory=AlwaysFailDecoder,
        retry_delays=(0.0, 0.0, 0.0),
    )
    updates = collect_until_terminal(session, TARGET_A)
    self.assertEqual(count_state(updates, PreviewState.RETRYING), 3)
    self.assertEqual(updates[-1].state, PreviewState.FAILED)
```

Also test offline resolution, explicit stop canceling retries, daemon worker creation, and close calling decoder close plus bounded join.

- [ ] **Step 2: Run tests and verify failure**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_preview.py -q
```

Expected: collection fails because `preview.py` does not exist.

- [ ] **Step 3: Implement preview lifecycle types**

```python
class PreviewState(str, Enum):
    IDLE = "idle"
    RESOLVING = "resolving"
    CONNECTING = "connecting"
    PLAYING = "playing"
    RETRYING = "retrying"
    OFFLINE = "offline"
    FAILED = "failed"
    STOPPED = "stopped"

@dataclass(frozen=True, slots=True)
class PreviewUpdate:
    generation: int
    target_id: str
    state: PreviewState
    message: str = ""
    frame: DecodedFrame | None = None
```

`PreviewSession.start(target, callback)` must stop the old decoder, increment generation, create a new stop event, and launch a named daemon thread. The worker calls `asyncio.run(resolver(target))`, emits `OFFLINE` for a non-live stream, and retries decoder failures exactly for `retry_delays`. Every emit rechecks generation and stop state under a lock. `stop()` invalidates the generation before closing the decoder so late callbacks are ignored.

- [ ] **Step 4: Run tests and commit**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_preview.py -q
git add lubo/apps/desktop/preview.py tests/apps/desktop/test_preview.py
git commit -m "feat: manage one desktop preview session"
```

Expected: all preview lifecycle tests pass.

### Task 5: Build the Stable Kivy Preview Pane

**Files:**
- Create: `lubo/apps/desktop/preview_widget.py`
- Create: `tests/apps/desktop/test_preview_widget.py`

- [ ] **Step 1: Write widget tests**

Use existing Kivy test isolation patterns and assert fixed aspect handling, texture updates, and status text:

```python
def test_frame_updates_texture_without_changing_pane_ratio(self):
    pane = PreviewPane(font_name=None)
    original_ratio = pane.preview_aspect_ratio
    pane.apply_update(
        PreviewUpdate(1, "target", PreviewState.PLAYING, frame=FRAME_640_360)
    )

    self.assertEqual(pane.video.texture.size, (640, 360))
    self.assertEqual(pane.preview_aspect_ratio, original_ratio)
    self.assertEqual(pane.video.color, [1, 1, 1, 1])

def test_stop_button_calls_bound_callback(self):
    stopped = []
    pane = PreviewPane(on_stop=lambda: stopped.append(True))
    pane.stop_button.trigger_action()
    self.assertEqual(stopped, [True])
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
$env:KIVY_NO_FILELOG='1'
$env:KIVY_LOG_MODE='PYTHON'
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_preview_widget.py -q
```

Expected: collection fails because `preview_widget.py` does not exist.

- [ ] **Step 3: Implement the pane**

Build `PreviewPane(BoxLayout)` with a compact header, an `Image` inside a 16:9 `FloatLayout`, status overlay, metadata label, and stop button. `apply_update()` creates or resizes a Kivy `Texture`, flips it vertically once, calls `blit_buffer(..., colorfmt="rgba", bufferfmt="ubyte")`, and updates only visible state; it must not log frame contents or source URLs.

- [ ] **Step 4: Run tests and commit**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_preview_widget.py -q
git add lubo/apps/desktop/preview_widget.py tests/apps/desktop/test_preview_widget.py
git commit -m "feat: render embedded desktop previews"
```

Expected: widget tests pass.

### Task 6: Integrate Target Actions and Two-Column Monitoring UI

**Files:**
- Modify: `lubo/apps/desktop/main.py`
- Modify: `tests/apps/desktop/test_controller.py`
- Create: `tests/apps/desktop/test_monitoring_ui.py`
- Modify: `tests/apps/desktop/test_logging.py`

- [ ] **Step 1: Write integration tests**

Cover preview selection, single-session switching, stop-and-pause closing matching preview, resume, callback marshalling through `Clock`, and shutdown order:

```python
def test_stop_and_pause_target_closes_matching_preview(self):
    root, controller, preview = self.make_root(preview_target_id="target-1")

    root._stop_and_pause_target("target-1")
    self.complete_background_action(root)

    controller.stop_and_pause_target.assert_called_once_with("target-1")
    preview.stop.assert_called_once_with()
    self.assertIn("Paused", root.status.text)

def test_app_stops_preview_before_scheduler_shutdown(self):
    order = []
    app = self.make_app(order=order)
    app.on_stop()
    self.assertLess(order.index("preview.close"), order.index("scheduler.shutdown"))
```

Extend logging tests with preview errors containing a signed URL and Cookie text; assert the persisted message contains redaction markers and no secret.

- [ ] **Step 2: Run tests and verify failure**

```powershell
$env:KIVY_NO_FILELOG='1'
$env:KIVY_LOG_MODE='PYTHON'
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop/test_monitoring_ui.py tests/apps/desktop/test_logging.py tests/apps/desktop/test_controller.py -q
```

Expected: FAIL because the preview service is not wired into `LuboDesktopApp` or `DesktopRoot`.

- [ ] **Step 3: Wire the preview service and layout**

In `LuboDesktopApp.build()`, construct:

```python
self.preview_session = PreviewSession(
    resolver=self.scheduler.resolve_preview_stream,
    decoder_factory=PyAvDecoder,
)
```

Pass it into `DesktopRoot`. Replace label-only target rendering with stable-height rows containing status text and state-specific buttons. Use a horizontal `BoxLayout` for the scrollable target column and `PreviewPane`, with explicit minimum widths and a 16:9 preview constraint. Schedule preview updates through `Clock.schedule_once` before touching widgets.

Run stop-and-pause and resume through `DaemonTaskQueue`; disable only the affected row while the action is pending. Close a matching preview immediately when stop-and-pause begins. In `on_stop()`, close preview before scheduler shutdown and executor shutdown.

- [ ] **Step 4: Run focused tests and commit**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/apps/desktop -q
git add lubo/apps/desktop/main.py tests/apps/desktop/test_controller.py tests/apps/desktop/test_monitoring_ui.py tests/apps/desktop/test_logging.py
git commit -m "feat: add preview monitoring workspace"
```

Expected: all desktop tests pass.

### Task 7: Add Desktop-Only PyAV Packaging

**Files:**
- Modify: `requirements-gui.txt`
- Modify: `pyproject.toml`
- Modify: `scripts/build_windows.ps1`
- Modify: `scripts/build_linux.sh`
- Create: `packaging/pyinstaller-hooks/hook-av.py`
- Modify: `tests/packaging/test_build_scripts.py`
- Modify: `README.md`

- [ ] **Step 1: Write dependency and packaging contract tests**

```python
def test_desktop_dependencies_pin_pyav_without_android_dependency(self):
    self.assertIn("av==17.0.1", self.gui_requirements)
    self.assertNotIn("av==17.0.1", self.android_requirements)

def test_pyav_hook_collects_dynamic_libraries(self):
    hook = (REPO_ROOT / "packaging/pyinstaller-hooks/hook-av.py").read_text()
    self.assertIn("collect_dynamic_libs", hook)
    self.assertIn("collect_submodules", hook)
```

Also assert both desktop build scripts retain `--additional-hooks-dir` and the Android build specification does not include `av`.

- [ ] **Step 2: Run packaging tests and verify failure**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pytest tests/packaging -q
```

Expected: FAIL because PyAV is not pinned and `hook-av.py` does not exist.

- [ ] **Step 3: Add dependency and hook**

Append `av==17.0.1` to `requirements-gui.txt` and the `gui` optional dependencies in `pyproject.toml`. Create:

```python
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

binaries = collect_dynamic_libs("av")
hiddenimports = collect_submodules("av")
```

Keep Android `requirements` unchanged. Document desktop preview as muted, single-target, and desktop-only; document `Stop and pause` and `Resume` behavior.

- [ ] **Step 4: Install the pinned dependency and run packaging tests**

```powershell
.\.build-venv\windows\Scripts\python.exe -m pip install "av==17.0.1"
.\.build-venv\windows\Scripts\python.exe -m pytest tests/packaging -q
```

Expected: packaging tests pass and Python imports `av` version `17.0.1`.

- [ ] **Step 5: Commit packaging changes**

```powershell
git add requirements-gui.txt pyproject.toml scripts/build_windows.ps1 scripts/build_linux.sh packaging/pyinstaller-hooks/hook-av.py tests/packaging/test_build_scripts.py README.md
git commit -m "build: package desktop preview runtime"
```

### Task 8: Full Verification, Real Streams, Build, and Release

**Files:**
- Modify only when a test exposes a specific defect.

- [ ] **Step 1: Run formatting and full automated tests**

```powershell
git diff --check
$env:KIVY_NO_FILELOG='1'
$env:KIVY_LOG_MODE='PYTHON'
.\.build-venv\windows\Scripts\python.exe -m pytest -q
```

Expected: all tests and 76 subtests pass.

- [ ] **Step 2: Real-test platform resolution, preview, recording, and stop**

For live Douyin, Bilibili, Huya, and Douyu targets:

1. Start monitoring and verify the target reaches recording state.
2. Start embedded preview and require visible changing frames for at least 15 seconds.
3. Switch to the next target and verify no old-target frame appears afterward.
4. Use `Stop and pause`; verify the process exits and the next monitoring check does not restart it.
5. Resume and verify a later monitoring check can record it again.
6. Confirm each short recording is non-empty and `ffprobe` reports a video stream.

Expected: all four platforms complete the workflow, subject to finding a currently live public room for each platform.

- [ ] **Step 3: Build and smoke-test Windows**

```powershell
$env:FFMPEG_PATH='D:\下载\ffmpeg-2025-02-26-git-99e2af4e78-essentials_build\ffmpeg-2025-02-26-git-99e2af4e78-essentials_build\bin\ffmpeg.exe'
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -SkipInstall -PythonExe 'C:\Users\张晟歌\AppData\Roaming\uv\python\cpython-3.13.2-windows-x86_64-none\python.exe'
```

Launch `dist/Lubo/Lubo.exe`, preview a live target, switch targets, stop preview, close the app, and assert no `Lubo`, preview worker, or recorder process remains.

- [ ] **Step 4: Push release commit and tag**

Sync the reviewed commits to `publish-main`, push `main`, create and push `v0.2.0-alpha.3`, then monitor `Build Desktop Apps`, `Build Android APK`, and `Publish Release Assets` to successful completion.

- [ ] **Step 5: Verify GitHub Release contents**

Use `gh release view v0.2.0-alpha.3 --json assets,body,isPrerelease,url` and verify:

- Windows ZIP, Linux ZIP, Android debug APK, and SHA256SUMS are present;
- the release body includes commit, pinned dependencies, build matrix, and changes;
- every checksum matches its uploaded asset;
- the release is marked prerelease.
