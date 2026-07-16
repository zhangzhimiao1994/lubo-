# Desktop Preview and Per-Target Stop Design

## Scope

This change applies to the Windows and Linux desktop application. It adds:

- one embedded, muted live preview at a time;
- per-target stop-and-pause behavior;
- explicit target resume behavior;
- target-level live and recording status in the monitoring UI.

Android remains unchanged. Preview audio, multi-view preview grids, seeking, and
recorded-file playback are out of scope.

## User Experience

The monitoring screen uses a two-column operational layout:

- The left column is a scrollable target list. Each row shows platform, display
  name, shortened URL, live state, recording state, and relevant actions.
- The right column contains a stable 16:9 preview pane. It shows the selected
  target, quality, muted state, connection state, and a stop-preview action.
- The event log remains below the monitoring workspace and scrolls separately.

Only one preview can run at a time. Selecting another target replaces the
current preview and displays a switching state until a frame arrives. Offline
targets report that they are not live without changing their monitoring state.

Target actions are state dependent:

- `Preview` starts or switches the preview.
- `Stop and pause` persists the disabled state, suppresses any pending start,
  stops an active recording, and closes the preview if it belongs to that
  target.
- `Resume` persists the enabled state and allows the next monitoring pass to
  process the target again.

## Architecture

### PreviewSession

`PreviewSession` is a desktop-only service with no Kivy widget dependency. It:

- obtains a live `StreamInfo` from the scheduler's current task when available,
  otherwise resolves the target through the same platform adapter and context;
- opens the selected stream with PyAV, including the resolver-provided safe
  headers and bounded open/read timeouts;
- decodes video only, scales output to at most 1280x720, and limits delivery to
  15 frames per second;
- sends immutable frame data and state callbacks to the UI;
- owns one worker thread and one PyAV container at a time;
- uses a monotonically increasing generation ID so callbacks from replaced
  sessions cannot update the current preview;
- closes the container and joins the worker with a bounded wait on stop.

The preview connection is separate from the FFmpeg recording connection.
Stopping or replacing a preview never stops recording.

### PreviewPane

`PreviewPane` owns the Kivy texture and visible preview states: idle, resolving,
connecting, playing, retrying, offline, failed, and stopped. Worker callbacks
are marshalled through `Clock` before touching Kivy objects. A new frame may
resize the texture but never resize the 16:9 pane.

The pane is muted by design. Audio streams are not decoded.

### Scheduler Suppression

The scheduler gains explicit target suppression in addition to the persisted
`enabled` field. Suppression closes the race where a target is paused while an
already claimed resolve operation is still in flight.

`pause_target(target_id)` adds the ID to the suppression set before stopping an
active process. The scheduler checks suppression when claiming targets and
again immediately before starting a recorder process. `resume_target(target_id)`
removes the ID from the set.

The desktop controller persists the disabled state before requesting scheduler
suppression. If process termination fails, the target remains paused and the UI
reports the stop error. It is never silently re-enabled.

## Data Flow

Preview start:

1. The user selects `Preview` for a target.
2. The UI stops the previous generation and enters resolving state.
3. `PreviewSession` reuses a current live `StreamInfo` or resolves a fresh one.
4. PyAV opens the selected FLV, HLS, or HTTP stream with safe headers.
5. The worker decodes and scales video frames.
6. The current generation delivers frame bytes to `PreviewPane` on the Kivy
   thread.

Stop and pause:

1. The controller writes the target as disabled to the URL store.
2. The scheduler suppresses the target and cancels any pending start path.
3. An active recorder process is stopped using the existing graceful-to-force
   cleanup sequence.
4. A matching preview generation is closed.
5. The target row changes to paused and exposes `Resume`.

## Failure Handling

Preview failures are isolated from recording. Network or decode failure retries
up to three times with bounded backoff unless the target is switched, preview is
stopped, or the application is closing. A fresh generation invalidates all old
callbacks.

Application shutdown stops preview before shutting down the scheduler and task
queue. The preview worker must not keep the process alive after the Kivy window
closes.

Persistent logs include event type, target ID, preview state, and sanitized
error text. They never include stream URLs, query signatures, cookies, command
payloads, or frame data.

## Dependencies and Packaging

The desktop GUI dependency set pins PyAV. PyAV is excluded from Android
requirements. Windows and Linux PyInstaller builds collect PyAV's extension
modules and bundled FFmpeg libraries. Existing standalone FFmpeg remains the
recorder backend.

The packaged application must start and close without missing dynamic libraries
or surviving preview threads on both desktop platforms.

## Verification

Automated tests cover:

- stop-and-pause persistence and scheduler suppression;
- pause during an in-flight resolve;
- resume behavior;
- single-preview replacement and stale callback rejection;
- offline, timeout, retry, decode failure, and explicit stop states;
- header forwarding and log redaction;
- preview worker cleanup during application shutdown;
- Windows and Linux packaging contracts for PyAV.

Manual desktop verification covers embedded muted preview, target switching,
recording independence, stop-and-pause, and resume. Windows real-stream checks
cover Douyin, Bilibili, Huya, and Douyu. A short recording regression confirms
that adding preview does not change recorder output or cleanup behavior.

## Acceptance Criteria

- A user can preview one live target inside the desktop app without audio.
- Switching previews cannot display frames from the previous target.
- A user can stop and pause one target without stopping other recordings.
- A paused target cannot restart until explicitly resumed, including when pause
  races with resolution.
- Preview errors do not interrupt recording.
- Closing the app leaves no preview or recorder process running.
- Windows and Linux release artifacts contain all required preview libraries.
