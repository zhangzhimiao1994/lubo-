from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from typing import Any

from lubo.core.events import EventBus, RecorderEvent, RecorderEventType
from lubo.core.models import OutputFormat, Quality, RecordingStatus, RecordingTarget, RecordingTask
from lubo.platforms.base import ResolveContext, UnsupportedPlatformError
from lubo.platforms.registry import PlatformRegistry
from lubo.recorders.ffmpeg import RecorderOptions


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    output_dir: Path
    quality: Quality
    proxy_addr: str = ""
    cookies: dict[str, str] | None = None
    output_format: OutputFormat = OutputFormat.TS
    split_enabled: bool = True
    split_seconds: int = 1800
    max_concurrency: int = 3
    convert_to_mp4: bool = False

    def __post_init__(self) -> None:
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than 0")


class RecordingScheduler:
    def __init__(self, registry: PlatformRegistry, recorder: Any, event_bus: EventBus, config: SchedulerConfig) -> None:
        self.registry = registry
        self.recorder = recorder
        self.event_bus = event_bus
        self.config = config
        self._tasks: dict[str, RecordingTask] = {}
        self._processes: dict[str, Any] = {}
        self._inflight: set[str] = set()
        self._stopping: set[str] = set()
        self._lifecycle_lock = RLock()
        self._stop_condition = Condition(self._lifecycle_lock)
        self._closed = False

    async def check_once(self, targets: list[RecordingTarget]) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._reap_exited_processes()

            claimed: list[RecordingTarget] = []
            for target in targets:
                if not target.enabled or target.id in self._processes or target.id in self._inflight:
                    continue
                self._inflight.add(target.id)
                claimed.append(target)

        semaphore = asyncio.Semaphore(self.config.max_concurrency)

        async def run_check(target: RecordingTarget) -> None:
            try:
                async with semaphore:
                    await self._check_target(target)
            except Exception as exc:
                self._fail_task(target, RecorderEventType.ERROR, exc)
            finally:
                with self._lifecycle_lock:
                    self._inflight.discard(target.id)

        await asyncio.gather(*(run_check(target) for target in claimed))

    def stop_target(self, target_id: str) -> None:
        with self._lifecycle_lock:
            if target_id in self._stopping:
                return
            process = self._processes.get(target_id)
            if process is None:
                return
            self._stopping.add(target_id)
            task = self._tasks.get(target_id)
            if task:
                task.status = RecordingStatus.STOPPING

        try:
            self.recorder.stop(process)
        except Exception as exc:
            force_succeeded = False
            force_error: Exception | None = None
            force_stop = getattr(self.recorder, "force_stop", None)
            if callable(force_stop):
                try:
                    force_stop(process)
                    force_succeeded = True
                except Exception as cleanup_exc:
                    force_error = cleanup_exc
            message = str(exc)
            if force_error is not None:
                message = f"{message}; forced cleanup failed: {force_error}"
            with self._lifecycle_lock:
                self._stopping.discard(target_id)
                if force_succeeded and self._processes.get(target_id) is process:
                    self._processes.pop(target_id, None)
                if task:
                    task.status = RecordingStatus.ERROR
                    task.last_error = message
                self._stop_condition.notify_all()
            self.event_bus.publish(
                RecorderEvent(
                    type=RecorderEventType.ERROR,
                    target_id=target_id,
                    message=message,
                )
            )
            return

        with self._lifecycle_lock:
            self._stopping.discard(target_id)
            if self._processes.get(target_id) is process:
                self._processes.pop(target_id, None)
            if task:
                task.status = RecordingStatus.IDLE
                task.last_error = ""
            self._stop_condition.notify_all()
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.RECORDING_STOPPED, target_id=target_id))

    def stop_all(self) -> None:
        attempted: set[str] = set()
        while True:
            with self._stop_condition:
                target_ids = [
                    target_id
                    for target_id in self._processes
                    if target_id not in self._stopping and target_id not in attempted
                ]
                if not target_ids:
                    if self._stopping:
                        self._stop_condition.wait()
                        continue
                    return
                attempted.update(target_ids)

            if len(target_ids) == 1:
                self.stop_target(target_ids[0])
                continue
            with ThreadPoolExecutor(
                max_workers=min(32, len(target_ids)),
                thread_name_prefix="recorder-stop",
            ) as executor:
                list(executor.map(self.stop_target, target_ids))

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            self._closed = True
        self.stop_all()
        with self._lifecycle_lock:
            remaining = list(self._processes)
        if remaining:
            raise RuntimeError(
                f"failed to stop {len(remaining)} recording process(es)"
            )

    @property
    def tasks(self) -> dict[str, RecordingTask]:
        with self._lifecycle_lock:
            return dict(self._tasks)

    def _reap_exited_processes(self) -> None:
        for target_id, process in list(self._processes.items()):
            if target_id in self._stopping:
                continue
            returncode = process.poll()
            if returncode is None:
                continue
            self._processes.pop(target_id, None)
            task = self._tasks.get(target_id)
            if returncode == 0:
                if task:
                    task.status = RecordingStatus.IDLE
                    task.last_error = ""
                self.event_bus.publish(
                    RecorderEvent(
                        type=RecorderEventType.RECORDING_STOPPED,
                        target_id=target_id,
                    )
                )
                continue
            message = f"recorder process exited with code {returncode}"
            if task:
                task.status = RecordingStatus.ERROR
                task.last_error = message
            self.event_bus.publish(
                RecorderEvent(
                    type=RecorderEventType.RECORDING_FAILED,
                    target_id=target_id,
                    message=message,
                )
            )

    async def _check_target(self, target: RecordingTarget) -> None:
        task = self._tasks.setdefault(target.id, RecordingTask(target=target))
        adapter = self.registry.match(target.url)
        if adapter is None:
            self._fail_task(
                target, RecorderEventType.ERROR, UnsupportedPlatformError()
            )
            return
        task.status = RecordingStatus.RESOLVING
        task.last_error = ""
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.RESOLVE_STARTED, target_id=target.id))
        context = ResolveContext(
            quality=target.quality or self.config.quality,
            proxy_addr=self.config.proxy_addr,
            cookies=self.config.cookies,
        )
        try:
            stream = await adapter.resolve(target, context)
        except Exception as exc:
            self._fail_task(target, RecorderEventType.ERROR, exc)
            return
        with self._lifecycle_lock:
            if self._closed:
                task.status = RecordingStatus.IDLE
                return
        task.stream = stream
        if not stream.is_live:
            task.status = RecordingStatus.IDLE
            self.event_bus.publish(RecorderEvent(type=RecorderEventType.OFFLINE_DETECTED, target_id=target.id))
            return
        task.status = RecordingStatus.LIVE
        self.event_bus.publish(
            RecorderEvent(
                type=RecorderEventType.LIVE_DETECTED,
                target_id=target.id,
                payload={"anchor": stream.anchor_name},
            )
        )
        options = RecorderOptions(
            output_format=self.config.output_format,
            split_enabled=self.config.split_enabled,
            split_seconds=self.config.split_seconds,
            convert_to_mp4=self.config.convert_to_mp4,
            proxy_addr=self.config.proxy_addr,
        )
        try:
            command = self.recorder.build_command(target, stream, self.config.output_dir, options)
        except Exception as exc:
            self._fail_task(target, RecorderEventType.RECORDING_FAILED, exc)
            return
        with self._lifecycle_lock:
            if self._closed:
                task.status = RecordingStatus.IDLE
                return
            try:
                process = self.recorder.start(command)
            except Exception as exc:
                self._fail_task(target, RecorderEventType.RECORDING_FAILED, exc)
                return
            self._processes[target.id] = process
            task.status = RecordingStatus.RECORDING
            self.event_bus.publish(
                RecorderEvent(
                    type=RecorderEventType.RECORDING_STARTED,
                    target_id=target.id,
                    payload={"command": command},
                )
            )

    def _fail_task(self, target: RecordingTarget, event_type: RecorderEventType, exc: Exception) -> None:
        task = self._tasks.setdefault(target.id, RecordingTask(target=target))
        task.status = RecordingStatus.ERROR
        task.last_error = str(exc)
        self.event_bus.publish(RecorderEvent(type=event_type, target_id=target.id, message=str(exc)))
