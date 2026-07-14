from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from douyinliverecorder.core.config import AppConfig, ConfigService
from douyinliverecorder.core.models import Quality, RecordingTarget
from douyinliverecorder.core.scheduler import RecordingScheduler
from douyinliverecorder.core.url_store import UrlStore


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
        self.targets: list[RecordingTarget] = self.url_store.load()
        self.scheduler = scheduler

    def add_target(
        self,
        url: str,
        quality: Quality | None = None,
        name: str = "",
    ) -> None:
        candidate = self.url_store.add(self.targets, url, quality, name)
        self.url_store.save(candidate)
        self.targets = candidate

    def remove_target(self, target_id: str) -> None:
        candidate = [
            target for target in self.targets if target.id != target_id
        ]
        if len(candidate) == len(self.targets):
            return
        self.url_store.save(candidate)
        self.targets = candidate

    def set_target_enabled(self, target_id: str, enabled: bool) -> None:
        for index, target in enumerate(self.targets):
            if target.id != target_id:
                continue
            candidate = list(self.targets)
            candidate[index] = replace(target, enabled=enabled)
            self.url_store.save(candidate)
            self.targets = candidate
            return

    async def check_once(self) -> None:
        await self.scheduler.check_once(self.targets)

    def stop_all(self) -> None:
        self.scheduler.stop_all()
