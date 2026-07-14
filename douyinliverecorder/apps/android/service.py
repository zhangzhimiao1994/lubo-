from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from douyinliverecorder.apps.android.platform import STOP_REQUEST_FILE, app_storage_root
from douyinliverecorder.apps.android.state import write_status
from douyinliverecorder.core.config import ConfigService
from douyinliverecorder.core.events import EventBus, RecorderEvent
from douyinliverecorder.core.models import RecordingStatus
from douyinliverecorder.core.scheduler import RecordingScheduler, SchedulerConfig
from douyinliverecorder.core.url_store import UrlStore
from douyinliverecorder.platforms.douyin import DouyinAdapter
from douyinliverecorder.platforms.registry import PlatformRegistry
from douyinliverecorder.recorders.http_stream import DirectHttpRecorder


logger = logging.getLogger(__name__)


def _configure_logging(root: Path) -> logging.Handler:
    log_path = root / "logs" / "service.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def _service_paths(root: Path) -> tuple[Path, Path, Path]:
    config_dir = root / "config"
    return (
        config_dir / "config.ini",
        config_dir / "URL_config.ini",
        root / "service_status.json",
    )


def _active_count(scheduler: RecordingScheduler) -> int:
    return sum(
        task.status == RecordingStatus.RECORDING
        for task in scheduler.tasks.values()
    )


def run_service(root: Path | None = None) -> None:
    storage_root = root or app_storage_root()
    log_handler = _configure_logging(storage_root)
    config_path, url_path, status_path = _service_paths(storage_root)
    config = ConfigService(config_path).load()
    targets = UrlStore(url_path, default_quality=config.quality).load()
    event_bus = EventBus()
    scheduler = RecordingScheduler(
        registry=PlatformRegistry([DouyinAdapter()]),
        recorder=DirectHttpRecorder(),
        event_bus=event_bus,
        config=SchedulerConfig(
            output_dir=storage_root / "recordings",
            quality=config.quality,
            proxy_addr=config.proxy_addr if config.use_proxy else "",
            cookies={"douyin": config.douyin_cookie},
            split_enabled=False,
            max_concurrency=min(config.max_concurrency, 2),
        ),
    )

    def publish_status(event: RecorderEvent) -> None:
        write_status(
            status_path,
            {
                "monitoring": True,
                "active_recordings": _active_count(scheduler),
                "message": event.message or event.type.value,
                "target_id": event.target_id,
                "updated_at": time.time(),
            },
        )

    event_bus.subscribe(publish_status)
    write_status(
        status_path,
        {
            "monitoring": True,
            "active_recordings": 0,
            "message": f"Monitoring {len(targets)} target(s)",
            "updated_at": time.time(),
        },
    )

    stop_request = storage_root / STOP_REQUEST_FILE
    try:
        while not stop_request.exists():
            asyncio.run(scheduler.check_once(targets))
            deadline = time.monotonic() + max(5, config.loop_seconds)
            while time.monotonic() < deadline and not stop_request.exists():
                time.sleep(1)
    except Exception:
        logger.exception("Android recorder service failed")
        write_status(
            status_path,
            {
                "monitoring": False,
                "active_recordings": _active_count(scheduler),
                "message": "Service failed; see logs/service.log",
                "updated_at": time.time(),
            },
        )
        raise
    finally:
        scheduler.shutdown()
        stop_request.unlink(missing_ok=True)
        write_status(
            status_path,
            {
                "monitoring": False,
                "active_recordings": 0,
                "message": "Stopped",
                "updated_at": time.time(),
            },
        )
        logger.removeHandler(log_handler)
        log_handler.close()


if __name__ == "__main__":
    run_service()
