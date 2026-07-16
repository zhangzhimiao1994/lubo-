import asyncio
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from lubo.core.events import EventBus, RecorderEventType
from lubo.core.models import Quality, RecordingStatus, RecordingTarget, StreamInfo
from lubo.core.scheduler import RecordingScheduler, SchedulerConfig
from lubo.platforms.base import ResolveContext, UnsupportedPlatformError
from lubo.platforms.registry import PlatformRegistry


class FakeAdapter:
    key = "douyin"
    display_name = "Douyin"

    def __init__(
        self,
        *,
        is_live=True,
        resolve_error=None,
        resolve_delay=0,
        resolve_started=None,
        resolve_gate=None,
    ):
        self.is_live = is_live
        self.resolve_error = resolve_error
        self.resolve_delay = resolve_delay
        self.resolve_started = resolve_started
        self.resolve_gate = resolve_gate
        self.resolve_calls = 0
        self.contexts = []

    def matches(self, url: str) -> bool:
        return "douyin.com" in url

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        self.resolve_calls += 1
        self.contexts.append(context)
        if self.resolve_started:
            self.resolve_started.set()
        if self.resolve_gate:
            await self.resolve_gate.wait()
        if self.resolve_delay:
            await asyncio.sleep(self.resolve_delay)
        if self.resolve_error:
            raise self.resolve_error
        return StreamInfo(
            platform_key="douyin",
            platform_name="Douyin",
            anchor_name="anchor-a",
            is_live=self.is_live,
            primary_url="https://pull.example/live.m3u8",
        )


class FakeProcess:
    def __init__(self, returncode=None):
        self.stopped = False
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeRecorder:
    def __init__(self, *, build_error=None, start_error=None, stop_error=None):
        self.commands = []
        self.processes = []
        self.process = FakeProcess()
        self.build_error = build_error
        self.start_error = start_error
        self.stop_error = stop_error

    def build_command(self, target, stream, output_dir, options):
        if self.build_error:
            raise self.build_error
        return ["ffmpeg", "-i", stream.primary_url, str(Path(output_dir) / "out.ts")]

    def start(self, command):
        if self.start_error:
            raise self.start_error
        self.commands.append(command)
        process = FakeProcess()
        self.processes.append(process)
        self.process = process
        return process

    def stop(self, process, timeout=10):
        if self.stop_error:
            raise self.stop_error
        process.stopped = True
        process.returncode = 0


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def make_scheduler(
        self,
        adapter=None,
        recorder=None,
        bus=None,
        max_concurrency=3,
        minimum_free_space_mb=0,
        disk_usage=None,
    ):
        kwargs = {}
        if disk_usage is not None:
            kwargs["disk_usage"] = disk_usage
        return RecordingScheduler(
            registry=PlatformRegistry([adapter or FakeAdapter()]),
            recorder=recorder or FakeRecorder(),
            event_bus=bus or EventBus(),
            config=SchedulerConfig(
                output_dir=Path("downloads"),
                quality=Quality.ORIGINAL,
                max_concurrency=max_concurrency,
                minimum_free_space_mb=minimum_free_space_mb,
            ),
            **kwargs,
        )

    def test_scheduler_config_rejects_non_positive_concurrency(self):
        with self.assertRaisesRegex(ValueError, "max_concurrency"):
            SchedulerConfig(
                output_dir=Path("downloads"),
                quality=Quality.ORIGINAL,
                max_concurrency=0,
            )

    def test_scheduler_config_rejects_negative_free_space_threshold(self):
        with self.assertRaisesRegex(ValueError, "minimum_free_space_mb"):
            SchedulerConfig(
                output_dir=Path("downloads"),
                quality=Quality.ORIGINAL,
                minimum_free_space_mb=-1,
            )

    async def test_low_disk_space_prevents_resolve_and_recording_start(self):
        adapter = FakeAdapter()
        recorder = FakeRecorder()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(
            adapter=adapter,
            recorder=recorder,
            bus=bus,
            minimum_free_space_mb=1024,
            disk_usage=lambda _path: SimpleNamespace(free=100 * 1024 * 1024),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(adapter.resolve_calls, 0)
        self.assertEqual(recorder.commands, [])
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.ERROR)
        self.assertIn("1024 MiB", scheduler.tasks[target.id].last_error)
        self.assertEqual(events[-1].type, RecorderEventType.RECORDING_FAILED)

    async def test_free_space_equal_to_threshold_allows_recording(self):
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(
            recorder=recorder,
            minimum_free_space_mb=1024,
            disk_usage=lambda _path: SimpleNamespace(free=1024 * 1024 * 1024),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 1)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)

    async def test_low_disk_space_stops_active_recording_on_next_check(self):
        free_values = iter(
            (2 * 1024**3, 2 * 1024**3, 100 * 1024**2)
        )
        recorder = FakeRecorder()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(
            recorder=recorder,
            bus=bus,
            minimum_free_space_mb=1024,
            disk_usage=lambda _path: SimpleNamespace(free=next(free_values)),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        process = recorder.process
        events.clear()

        await scheduler.check_once([target])

        self.assertTrue(process.stopped)
        self.assertNotIn(target.id, scheduler._processes)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.ERROR)
        self.assertEqual(
            [event.type for event in events],
            [RecorderEventType.RECORDING_STOPPED, RecorderEventType.RECORDING_FAILED],
        )

    async def test_disk_inspection_failure_prevents_recording(self):
        def fail_disk_usage(_path):
            raise OSError("volume unavailable")

        scheduler = self.make_scheduler(
            minimum_free_space_mb=1024,
            disk_usage=fail_disk_usage,
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        with self.assertLogs("lubo.core.scheduler", level="WARNING") as captured:
            await scheduler.check_once([target])

        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.ERROR)
        self.assertIn("could not check free disk space", scheduler.tasks[target.id].last_error)
        output = "\n".join(captured.output)
        self.assertIn("OSError", output)
        self.assertIn("output_path=relative(depth=1)", output)
        self.assertNotIn("volume unavailable", output)

    async def test_zero_free_space_threshold_disables_disk_inspection(self):
        def unexpected_disk_usage(_path):
            raise AssertionError("disk usage should not be called")

        recorder = FakeRecorder()
        scheduler = self.make_scheduler(
            recorder=recorder,
            minimum_free_space_mb=0,
            disk_usage=unexpected_disk_usage,
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 1)

    async def test_check_once_honors_max_concurrency(self):
        class TrackingAdapter(FakeAdapter):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.peak = 0

            async def resolve(self, target, context):
                self.active += 1
                self.peak = max(self.peak, self.active)
                try:
                    await asyncio.sleep(0.02)
                    return await super().resolve(target, context)
                finally:
                    self.active -= 1

        adapter = TrackingAdapter()
        scheduler = self.make_scheduler(adapter=adapter, max_concurrency=2)
        targets = [
            RecordingTarget(url=f"https://live.douyin.com/{index}")
            for index in range(5)
        ]

        await scheduler.check_once(targets)

        self.assertEqual(adapter.peak, 2)

    async def test_slow_target_does_not_block_other_target(self):
        slow_started = asyncio.Event()
        slow_gate = asyncio.Event()

        class SelectiveAdapter(FakeAdapter):
            async def resolve(self, target, context):
                if target.url.endswith("/slow"):
                    slow_started.set()
                    await slow_gate.wait()
                return await super().resolve(target, context)

        recorder = FakeRecorder()
        scheduler = self.make_scheduler(
            adapter=SelectiveAdapter(),
            recorder=recorder,
            max_concurrency=2,
        )
        slow = RecordingTarget(url="https://live.douyin.com/slow")
        fast = RecordingTarget(url="https://live.douyin.com/fast")

        check = asyncio.create_task(scheduler.check_once([slow, fast]))
        await slow_started.wait()
        for _ in range(20):
            if fast.id in scheduler._processes:
                break
            await asyncio.sleep(0.005)

        self.assertIn(fast.id, scheduler._processes)
        slow_gate.set()
        await check

    async def test_stop_all_stops_processes_concurrently(self):
        class BlockingStopRecorder(FakeRecorder):
            def __init__(self):
                super().__init__()
                self.barrier = threading.Barrier(2)

            def stop(self, process, timeout=10):
                self.barrier.wait(timeout=1)
                time.sleep(0.02)
                super().stop(process, timeout)

        recorder = BlockingStopRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        targets = [
            RecordingTarget(url=f"https://live.douyin.com/{index}")
            for index in range(2)
        ]
        await scheduler.check_once(targets)

        scheduler.stop_all()

        self.assertEqual(scheduler._processes, {})
        self.assertTrue(all(process.stopped for process in recorder.processes))

    async def test_shutdown_waits_for_stop_already_in_progress(self):
        stop_started = threading.Event()
        stop_gate = threading.Event()

        class GatedRecorder(FakeRecorder):
            def stop(self, process, timeout=10):
                stop_started.set()
                if not stop_gate.wait(timeout=2):
                    raise TimeoutError("test stop gate timed out")
                super().stop(process, timeout)

        recorder = GatedRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        stop_thread = threading.Thread(target=scheduler.stop_target, args=(target.id,))
        stop_thread.start()
        self.assertTrue(stop_started.wait(timeout=1))

        shutdown_thread = threading.Thread(target=scheduler.shutdown)
        shutdown_thread.start()
        time.sleep(0.03)

        self.assertTrue(shutdown_thread.is_alive())
        stop_gate.set()
        stop_thread.join(timeout=1)
        shutdown_thread.join(timeout=1)
        self.assertFalse(shutdown_thread.is_alive())
        self.assertEqual(scheduler._processes, {})

    async def test_shutdown_force_stops_after_graceful_stop_failure(self):
        class ForceRecorder(FakeRecorder):
            def __init__(self):
                super().__init__(stop_error=RuntimeError("graceful stop failed"))
                self.force_calls = 0

            def force_stop(self, process):
                self.force_calls += 1
                process.stopped = True
                process.returncode = -9

        recorder = ForceRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])

        scheduler.shutdown()

        self.assertEqual(recorder.force_calls, 1)
        self.assertEqual(scheduler._processes, {})
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.ERROR)

    async def test_shutdown_raises_when_process_cannot_be_stopped(self):
        recorder = FakeRecorder(stop_error=RuntimeError("stop failed"))
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])

        with self.assertRaisesRegex(RuntimeError, "failed to stop"):
            scheduler.shutdown()

        self.assertIn(target.id, scheduler._processes)

    async def test_check_once_starts_recording_for_live_target(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 1)
        self.assertIn(RecorderEventType.RECORDING_STARTED, [event.type for event in events])

    async def test_check_passes_platform_cookie_to_adapter(self):
        adapter = FakeAdapter(is_live=False)
        scheduler = RecordingScheduler(
            registry=PlatformRegistry([adapter]),
            recorder=FakeRecorder(),
            event_bus=EventBus(),
            config=SchedulerConfig(
                output_dir=Path("downloads"),
                quality=Quality.ORIGINAL,
                cookies={"douyin": "sessionid=test"},
                minimum_free_space_mb=0,
            ),
        )

        await scheduler.check_once([RecordingTarget(url="https://live.douyin.com/123")])

        self.assertEqual(adapter.contexts[0].cookies, {"douyin": "sessionid=test"})

    async def test_scheduled_check_matches_once_and_resolves_matched_adapter(self):
        selected_adapter = FakeAdapter()
        later_adapter = FakeAdapter(is_live=False)

        class StatefulRegistry(PlatformRegistry):
            def __init__(self):
                super().__init__()
                self.match_calls = 0

            def match(self, url):
                self.match_calls += 1
                if self.match_calls == 1:
                    return selected_adapter
                return later_adapter

        registry = StatefulRegistry()
        scheduler = RecordingScheduler(
            registry=registry,
            recorder=FakeRecorder(),
            event_bus=EventBus(),
            config=SchedulerConfig(
                output_dir=Path("downloads"),
                quality=Quality.ORIGINAL,
                minimum_free_space_mb=0,
            ),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(registry.match_calls, 1)
        self.assertEqual(selected_adapter.resolve_calls, 1)
        self.assertEqual(later_adapter.resolve_calls, 0)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)

    async def test_stop_target_stops_running_process(self):
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])
        scheduler.stop_target(target.id)

        self.assertTrue(recorder.process.stopped)

    async def test_pause_during_resolve_prevents_recording_without_failure(self):
        resolve_started = asyncio.Event()
        resolve_gate = asyncio.Event()
        adapter = FakeAdapter(
            resolve_started=resolve_started,
            resolve_gate=resolve_gate,
        )
        recorder = FakeRecorder()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(
            adapter=adapter,
            recorder=recorder,
            bus=bus,
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        check = asyncio.create_task(scheduler.check_once([target]))
        await resolve_started.wait()
        scheduler.pause_target(target.id)
        resolve_gate.set()
        await check

        self.assertEqual(recorder.commands, [])
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.IDLE)
        self.assertEqual(scheduler.tasks[target.id].last_error, "")
        self.assertNotIn(
            RecorderEventType.RECORDING_FAILED,
            [event.type for event in events],
        )
        self.assertNotIn(
            RecorderEventType.ERROR,
            [event.type for event in events],
        )

    async def test_pause_during_resolve_exception_cancels_failure(self):
        resolve_started = asyncio.Event()
        resolve_gate = asyncio.Event()
        adapter = FakeAdapter(
            resolve_error=RuntimeError("resolve failed"),
            resolve_started=resolve_started,
            resolve_gate=resolve_gate,
        )
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(adapter=adapter, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        check = asyncio.create_task(scheduler.check_once([target]))
        await resolve_started.wait()
        scheduler.pause_target(target.id)
        resolve_gate.set()
        await check

        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.IDLE)
        self.assertEqual(task.last_error, "")
        self.assertNotIn(
            RecorderEventType.ERROR,
            [event.type for event in events],
        )
        self.assertNotIn(
            RecorderEventType.RECORDING_FAILED,
            [event.type for event in events],
        )

    async def test_resume_allows_a_later_check_to_start_recording(self):
        adapter = FakeAdapter()
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(adapter=adapter, recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        scheduler.pause_target(target.id)
        await scheduler.check_once([target])
        scheduler.resume_target(target.id)
        await scheduler.check_once([target])

        self.assertEqual(adapter.resolve_calls, 1)
        self.assertEqual(len(recorder.commands), 1)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)

    async def test_paused_target_is_excluded_when_claiming_checks(self):
        adapter = FakeAdapter()
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(adapter=adapter, recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        scheduler.pause_target(target.id)
        await scheduler.check_once([target])

        self.assertEqual(adapter.resolve_calls, 0)
        self.assertEqual(recorder.commands, [])
        self.assertNotIn(target.id, scheduler.tasks)

    async def test_pause_target_stops_active_recording(self):
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        process = recorder.process

        scheduler.pause_target(target.id)

        self.assertTrue(process.stopped)
        self.assertNotIn(target.id, scheduler._processes)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.IDLE)

    async def test_pause_target_reports_when_active_recording_cannot_stop(self):
        recorder = FakeRecorder(stop_error=RuntimeError("stop failed"))
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        process = recorder.process

        with self.assertRaisesRegex(RuntimeError, "Unable to stop target recording"):
            scheduler.pause_target(target.id)

        self.assertIn(target.id, scheduler._suppressed)
        self.assertIs(scheduler._processes[target.id], process)
        self.assertFalse(process.stopped)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.ERROR)

    def test_pause_waiting_on_start_stops_new_process(self):
        start_entered = threading.Event()
        start_release = threading.Event()
        pause_attempted = threading.Event()

        class BlockingStartRecorder(FakeRecorder):
            def start(self, command):
                start_entered.set()
                if not start_release.wait(timeout=2):
                    raise TimeoutError("test start gate timed out")
                return super().start(command)

        recorder = BlockingStartRecorder()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")
        thread_errors = []

        def run_check():
            try:
                asyncio.run(scheduler.check_once([target]))
            except Exception as exc:
                thread_errors.append(exc)

        def run_pause():
            try:
                pause_attempted.set()
                scheduler.pause_target(target.id)
            except Exception as exc:
                thread_errors.append(exc)

        check_thread = threading.Thread(target=run_check)
        pause_thread = threading.Thread(target=run_pause)
        check_thread.start()
        try:
            self.assertTrue(start_entered.wait(timeout=1))
            pause_thread.start()
            self.assertTrue(pause_attempted.wait(timeout=1))
            self.assertTrue(pause_thread.is_alive())
        finally:
            start_release.set()
            check_thread.join(timeout=2)
            if pause_thread.ident is not None:
                pause_thread.join(timeout=2)

        self.assertFalse(check_thread.is_alive())
        self.assertFalse(pause_thread.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertTrue(recorder.processes)
        self.assertTrue(all(process.stopped for process in recorder.processes))
        self.assertNotIn(target.id, scheduler._processes)
        self.assertIn(target.id, scheduler._suppressed)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.IDLE)
        self.assertNotIn(
            RecorderEventType.RECORDING_FAILED,
            [event.type for event in events],
        )

    async def test_preview_reuses_stream_for_active_recording_process(self):
        adapter = FakeAdapter()
        scheduler = self.make_scheduler(adapter=adapter)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        recorded_stream = scheduler.tasks[target.id].stream
        process = scheduler._processes[target.id]

        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)
        self.assertIsNone(process.poll())

        preview_stream = await scheduler.resolve_preview_stream(target)

        self.assertIs(preview_stream, recorded_stream)
        self.assertEqual(adapter.resolve_calls, 1)

    async def test_preview_fresh_resolves_when_recording_process_is_missing(self):
        adapter = FakeAdapter()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(adapter=adapter, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        recorded_stream = scheduler.tasks[target.id].stream
        events.clear()
        with scheduler._lifecycle_lock:
            scheduler._processes.pop(target.id)

        preview_stream = await scheduler.resolve_preview_stream(target)

        self.assertIsNot(preview_stream, recorded_stream)
        self.assertEqual(adapter.resolve_calls, 2)
        self.assertEqual(events, [])
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)
        self.assertIs(scheduler.tasks[target.id].stream, recorded_stream)

    async def test_preview_fresh_resolves_when_recording_process_exited(self):
        adapter = FakeAdapter()
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(adapter=adapter, recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        recorded_stream = scheduler.tasks[target.id].stream
        recorder.process.returncode = 0

        preview_stream = await scheduler.resolve_preview_stream(target)

        self.assertIsNot(preview_stream, recorded_stream)
        self.assertEqual(adapter.resolve_calls, 2)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)
        self.assertIs(scheduler.tasks[target.id].stream, recorded_stream)

    async def test_preview_fresh_resolve_does_not_publish_or_mutate_task(self):
        adapter = FakeAdapter(is_live=False)
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(adapter=adapter, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")
        await scheduler.check_once([target])
        task_before = scheduler.tasks[target.id]
        stream_before = task_before.stream
        events.clear()
        adapter.is_live = True

        preview_stream = await scheduler.resolve_preview_stream(target)

        task_after = scheduler.tasks[target.id]
        self.assertTrue(preview_stream.is_live)
        self.assertEqual(adapter.resolve_calls, 2)
        self.assertEqual(events, [])
        self.assertIs(task_after, task_before)
        self.assertIs(task_after.stream, stream_before)
        self.assertEqual(task_after.status, RecordingStatus.IDLE)

    async def test_preview_unsupported_target_raises_without_events_or_task(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(bus=bus)
        target = RecordingTarget(url="https://example.com/123")

        with self.assertRaises(UnsupportedPlatformError):
            await scheduler.resolve_preview_stream(target)

        self.assertEqual(events, [])
        self.assertNotIn(target.id, scheduler.tasks)

    async def test_unsupported_url_publishes_error_without_process(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://example.com/123")
        failures = []
        fail_task = scheduler._fail_task

        def capture_failure(failed_target, event_type, exc):
            failures.append((failed_target, event_type, exc))
            fail_task(failed_target, event_type, exc)

        scheduler._fail_task = capture_failure

        await scheduler.check_once([target])

        self.assertEqual(recorder.commands, [])
        self.assertNotIn(target.id, scheduler._processes)
        self.assertEqual([event.type for event in events], [RecorderEventType.ERROR])
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0][2], UnsupportedPlatformError)
        self.assertEqual(scheduler.tasks[target.id].last_error, "Unsupported platform URL")
        self.assertNotIn(target.url, scheduler.tasks[target.id].last_error)

    async def test_unsupported_target_does_not_block_supported_target(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        unsupported = RecordingTarget(url="https://example.com/unknown")
        supported = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([unsupported, supported])

        self.assertEqual(
            scheduler.tasks[unsupported.id].status, RecordingStatus.ERROR
        )
        self.assertEqual(
            scheduler.tasks[supported.id].status, RecordingStatus.RECORDING
        )
        unsupported_events = [
            event.type for event in events if event.target_id == unsupported.id
        ]
        supported_events = [
            event.type for event in events if event.target_id == supported.id
        ]
        self.assertEqual(unsupported_events, [RecorderEventType.ERROR])
        self.assertIn(RecorderEventType.RECORDING_STARTED, supported_events)
        self.assertNotIn(RecorderEventType.ERROR, supported_events)

    async def test_offline_stream_publishes_offline_and_leaves_task_idle(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(adapter=FakeAdapter(is_live=False), bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.IDLE)
        self.assertIn(RecorderEventType.OFFLINE_DETECTED, [event.type for event in events])

    async def test_resolve_exception_sets_task_error_and_publishes_error(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(adapter=FakeAdapter(resolve_error=RuntimeError("resolve failed")), bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.ERROR)
        self.assertEqual(task.last_error, "resolve failed")
        self.assertEqual(events[-1].type, RecorderEventType.ERROR)
        self.assertEqual(events[-1].message, "resolve failed")

    async def test_recorder_build_exception_sets_task_error_and_publishes_recording_failed(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder(build_error=RuntimeError("build failed"))
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.ERROR)
        self.assertEqual(task.last_error, "build failed")
        self.assertEqual(events[-1].type, RecorderEventType.RECORDING_FAILED)
        self.assertEqual(events[-1].message, "build failed")
        self.assertNotIn(target.id, scheduler._processes)

    async def test_pause_during_build_exception_cancels_failure(self):
        build_started = threading.Event()
        build_release = threading.Event()

        class BlockingBuildRecorder(FakeRecorder):
            def build_command(self, target, stream, output_dir, options):
                build_started.set()
                if not build_release.wait(timeout=2):
                    raise TimeoutError("test build gate timed out")
                raise RuntimeError("build failed")

        recorder = BlockingBuildRecorder()
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        def pause_and_release():
            if build_started.wait(timeout=1):
                scheduler.pause_target(target.id)
            build_release.set()

        pause_thread = threading.Thread(target=pause_and_release)
        pause_thread.start()
        try:
            await scheduler.check_once([target])
        finally:
            build_release.set()
            pause_thread.join(timeout=2)

        self.assertFalse(pause_thread.is_alive())
        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.IDLE)
        self.assertEqual(task.last_error, "")
        self.assertNotIn(
            RecorderEventType.RECORDING_FAILED,
            [event.type for event in events],
        )
        self.assertNotIn(RecorderEventType.ERROR, [event.type for event in events])

    async def test_pause_during_post_resolve_disk_error_cancels_failure(self):
        disk_started = threading.Event()
        disk_release = threading.Event()
        disk_calls = 0

        def blocking_disk_usage(_path):
            nonlocal disk_calls
            disk_calls += 1
            if disk_calls == 1:
                return SimpleNamespace(free=2 * 1024**3)
            disk_started.set()
            if not disk_release.wait(timeout=2):
                raise TimeoutError("test disk gate timed out")
            return SimpleNamespace(free=100 * 1024**2)

        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        scheduler = self.make_scheduler(
            bus=bus,
            minimum_free_space_mb=1024,
            disk_usage=blocking_disk_usage,
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        def pause_and_release():
            if disk_started.wait(timeout=1):
                scheduler.pause_target(target.id)
            disk_release.set()

        pause_thread = threading.Thread(target=pause_and_release)
        pause_thread.start()
        try:
            await scheduler.check_once([target])
        finally:
            disk_release.set()
            pause_thread.join(timeout=2)

        self.assertFalse(pause_thread.is_alive())
        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.IDLE)
        self.assertEqual(task.last_error, "")
        self.assertNotIn(
            RecorderEventType.RECORDING_FAILED,
            [event.type for event in events],
        )
        self.assertNotIn(RecorderEventType.ERROR, [event.type for event in events])

    async def test_recorder_start_exception_sets_task_error_and_publishes_recording_failed(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder(start_error=RuntimeError("start failed"))
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        task = scheduler.tasks[target.id]
        self.assertEqual(task.status, RecordingStatus.ERROR)
        self.assertEqual(task.last_error, "start failed")
        self.assertEqual(events[-1].type, RecorderEventType.RECORDING_FAILED)
        self.assertEqual(events[-1].message, "start failed")
        self.assertNotIn(target.id, scheduler._processes)

    async def test_concurrent_check_once_for_same_target_starts_one_process(self):
        adapter = FakeAdapter(resolve_delay=0.01)
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(adapter=adapter, recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await asyncio.gather(scheduler.check_once([target]), scheduler.check_once([target]))

        self.assertEqual(adapter.resolve_calls, 1)
        self.assertEqual(len(recorder.commands), 1)
        self.assertIn(target.id, scheduler._processes)

    async def test_successfully_exited_process_is_reaped_and_restarted(self):
        bus = EventBus()
        events = []
        reaped_states = []
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        def capture_event(event):
            events.append(event)
            if event.type == RecorderEventType.RECORDING_STOPPED:
                task = scheduler.tasks[event.target_id]
                reaped_states.append((task.status, task.last_error))

        bus.subscribe(capture_event)

        await scheduler.check_once([target])
        recorder.process.returncode = 0
        events.clear()

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 2)
        self.assertEqual(events[0].type, RecorderEventType.RECORDING_STOPPED)
        self.assertEqual(events[-1].type, RecorderEventType.RECORDING_STARTED)
        self.assertEqual(reaped_states, [(RecordingStatus.IDLE, "")])
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)

    async def test_failed_process_is_reaped_reports_error_and_restarts(self):
        bus = EventBus()
        events = []
        reaped_states = []
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        def capture_event(event):
            events.append(event)
            if event.type == RecorderEventType.RECORDING_FAILED:
                task = scheduler.tasks[event.target_id]
                reaped_states.append((task.status, task.last_error))

        bus.subscribe(capture_event)

        await scheduler.check_once([target])
        recorder.process.returncode = 7
        events.clear()

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 2)
        self.assertEqual(events[0].type, RecorderEventType.RECORDING_FAILED)
        self.assertIn("7", events[0].message)
        self.assertEqual(events[-1].type, RecorderEventType.RECORDING_STARTED)
        self.assertEqual(reaped_states[0][0], RecordingStatus.ERROR)
        self.assertIn("7", reaped_states[0][1])
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.RECORDING)

    async def test_shutdown_during_resolve_prevents_recording_start(self):
        resolve_started = asyncio.Event()
        resolve_gate = asyncio.Event()
        adapter = FakeAdapter(
            resolve_started=resolve_started,
            resolve_gate=resolve_gate,
        )
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(adapter=adapter, recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        check = asyncio.create_task(scheduler.check_once([target]))
        await resolve_started.wait()
        scheduler.shutdown()
        resolve_gate.set()
        await check

        self.assertEqual(recorder.commands, [])
        await scheduler.check_once([target])
        self.assertEqual(adapter.resolve_calls, 1)

    async def test_shutdown_stops_existing_process(self):
        recorder = FakeRecorder()
        scheduler = self.make_scheduler(recorder=recorder)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])
        process = recorder.process
        scheduler.shutdown()

        self.assertTrue(process.stopped)
        self.assertNotIn(target.id, scheduler._processes)
        self.assertEqual(scheduler.tasks[target.id].status, RecordingStatus.IDLE)

    async def test_stop_exception_keeps_process_handle_and_publishes_error(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder(stop_error=RuntimeError("stop failed"))
        scheduler = self.make_scheduler(recorder=recorder, bus=bus)
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])
        process = recorder.process
        scheduler.stop_target(target.id)

        task = scheduler.tasks[target.id]
        self.assertIs(scheduler._processes[target.id], process)
        self.assertEqual(task.status, RecordingStatus.ERROR)
        self.assertEqual(task.last_error, "stop failed")
        self.assertFalse(process.stopped)
        self.assertEqual(events[-1].type, RecorderEventType.ERROR)
        self.assertEqual(events[-1].message, "stop failed")


if __name__ == "__main__":
    unittest.main()
