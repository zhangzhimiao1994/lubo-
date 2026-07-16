from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock

from lubo.core.config import AppConfig, ConfigService
from lubo.core.models import Quality, RecordingTarget
from lubo.core.scheduler import RecordingScheduler
from lubo.core.url_store import UrlStore


class DesktopController:
    def __init__(
        self,
        config_service: ConfigService,
        url_file: str | Path,
        scheduler: RecordingScheduler,
    ) -> None:
        self.config_service = config_service
        self.config: AppConfig = config_service.load()
        self.url_store = UrlStore(url_file, default_quality=self.config.quality)
        self._targets_lock = RLock()
        self.targets: list[RecordingTarget] = self.url_store.load()
        self.scheduler = scheduler

    def add_target(
        self,
        url: str,
        quality: Quality | None = None,
        name: str = "",
    ) -> None:
        with self._targets_lock:
            candidate = self.url_store.add(self.targets, url, quality, name)
            self.url_store.save(candidate)
            self.targets = candidate

    def remove_target(self, target_id: str) -> None:
        with self._targets_lock:
            candidate = [
                target for target in self.targets if target.id != target_id
            ]
            if len(candidate) == len(self.targets):
                return
            self.url_store.save(candidate)
            self.targets = candidate

    def target_by_id(self, target_id: str) -> RecordingTarget | None:
        with self._targets_lock:
            return next(
                (target for target in self.targets if target.id == target_id),
                None,
            )

    def set_target_enabled(self, target_id: str, enabled: bool) -> None:
        with self._targets_lock:
            self._set_target_enabled_locked(target_id, enabled)

    def _set_target_enabled_locked(
        self,
        target_id: str,
        enabled: bool,
    ) -> bool:
        for index, target in enumerate(self.targets):
            if target.id != target_id:
                continue
            candidate = list(self.targets)
            candidate[index] = replace(target, enabled=enabled)
            self.url_store.save(candidate)
            self.targets = candidate
            return True
        return False

    def stop_and_pause_target(self, target_id: str) -> None:
        with self._targets_lock:
            changed = self._set_target_enabled_locked(target_id, False)
        if not changed:
            return
        self.scheduler.pause_target(target_id)

    def resume_target(self, target_id: str) -> None:
        with self._targets_lock:
            changed = self._set_target_enabled_locked(target_id, True)
        if not changed:
            return
        self.scheduler.resume_target(target_id)

    async def check_once(self) -> None:
        with self._targets_lock:
            targets = list(self.targets)
        await self.scheduler.check_once(targets)

    def stop_all(self) -> None:
        self.scheduler.stop_all()
