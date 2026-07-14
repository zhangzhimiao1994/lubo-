# Core Douyin Desktop Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first refactored working slice: typed recording core, Douyin platform adapter, FFmpeg runner wrapper, and a Windows/Linux Kivy desktop GUI that can add a Douyin URL, monitor it, start recording, and stop cleanly.

**Architecture:** Keep the existing `main.py` entry point intact while adding a new `douyinliverecorder` package. The new package wraps existing `src.spider` and `src.stream` functions behind typed adapters and exposes a scheduler API that GUI code can drive without touching global variables.

**Tech Stack:** Python 3.10+, stdlib `unittest`, `asyncio`, existing `httpx`/`requests` parsing code, FFmpeg subprocesses, Kivy for desktop GUI, PyInstaller packaging scaffolding.

---

## Scope Boundary

This plan implements only the first independently testable slice from the approved spec:

- Core models and events.
- Backward-compatible config and URL file services.
- Platform adapter registry.
- Douyin adapter using existing `src.spider` and `src.stream`.
- FFmpeg command builder and process wrapper.
- Scheduler with bounded monitoring loop and stop controls.
- Minimal Windows/Linux Kivy GUI.
- Desktop packaging scaffolding.

Android foreground service, Android FFmpeg integration, and the remaining MVP platform adapters are separate implementation plans after this slice is verified.

## File Structure

Create:

- `douyinliverecorder/__init__.py` - package marker and version export.
- `douyinliverecorder/core/__init__.py` - core package exports.
- `douyinliverecorder/core/models.py` - dataclasses shared by scheduler, adapters, and recorder.
- `douyinliverecorder/core/events.py` - event types and synchronous event bus.
- `douyinliverecorder/core/config.py` - typed config loader/saver over existing `config/config.ini`.
- `douyinliverecorder/core/url_store.py` - backward-compatible target list over `config/URL_config.ini`.
- `douyinliverecorder/platforms/__init__.py` - platform package exports.
- `douyinliverecorder/platforms/base.py` - adapter protocol and resolve context.
- `douyinliverecorder/platforms/registry.py` - adapter registration and matching.
- `douyinliverecorder/platforms/douyin.py` - Douyin adapter.
- `douyinliverecorder/recorders/__init__.py` - recorder package exports.
- `douyinliverecorder/recorders/ffmpeg.py` - FFmpeg command builder and process runner.
- `douyinliverecorder/core/scheduler.py` - monitoring and recording orchestration.
- `douyinliverecorder/apps/__init__.py` - app package marker.
- `douyinliverecorder/apps/desktop/__init__.py` - desktop app package marker.
- `douyinliverecorder/apps/desktop/controller.py` - GUI-facing controller.
- `douyinliverecorder/apps/desktop/main.py` - Kivy app entry point.
- `requirements-gui.txt` - GUI-only dependency list.
- `scripts/build_windows.ps1` - Windows package scaffold.
- `scripts/build_linux.sh` - Linux package scaffold.
- `tests/core/test_models.py` - model tests.
- `tests/core/test_events.py` - event bus tests.
- `tests/core/test_config.py` - config wrapper tests.
- `tests/core/test_url_store.py` - URL store tests.
- `tests/platforms/test_registry.py` - platform registry tests.
- `tests/platforms/test_douyin_adapter.py` - Douyin adapter tests with mocked fetchers.
- `tests/recorders/test_ffmpeg.py` - FFmpeg command tests.
- `tests/core/test_scheduler.py` - scheduler orchestration tests.
- `tests/apps/desktop/test_controller.py` - desktop controller tests.

Modify:

- `pyproject.toml` - include package metadata for the new package and optional GUI dependency group.
- `.gitignore` - add build artifacts if missing.

Do not modify:

- `main.py` behavior.
- `src/spider.py` platform parsing internals.
- `src/stream.py` quality selection internals.
- `config/config.ini` default content.
- `config/URL_config.ini` default content.

---

### Task 1: Core Models

**Files:**
- Create: `douyinliverecorder/__init__.py`
- Create: `douyinliverecorder/core/__init__.py`
- Create: `douyinliverecorder/core/models.py`
- Create: `tests/core/test_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/core/test_models.py`:

```python
import unittest

from douyinliverecorder.core.models import (
    OutputFormat,
    Quality,
    RecordingStatus,
    RecordingTarget,
    StreamInfo,
)


class RecordingModelTests(unittest.TestCase):
    def test_recording_target_normalizes_url_and_defaults(self):
        target = RecordingTarget(url=" live.douyin.com/123456 ")

        self.assertEqual(target.url, "https://live.douyin.com/123456")
        self.assertTrue(target.enabled)
        self.assertEqual(target.quality, Quality.ORIGINAL)
        self.assertEqual(target.display_name, "")
        self.assertTrue(target.id)

    def test_stream_info_identifies_not_live_without_url(self):
        info = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="alice")

        self.assertFalse(info.is_live)
        self.assertEqual(info.primary_url, "")

    def test_stream_info_accepts_recording_urls(self):
        info = StreamInfo(
            platform_key="douyin",
            platform_name="Douyin",
            anchor_name="alice",
            title="test",
            is_live=True,
            quality=Quality.ORIGINAL,
            primary_url="https://example.com/live.m3u8",
            flv_url="https://example.com/live.flv",
            hls_url="https://example.com/live.m3u8",
            headers={"referer": "https://live.douyin.com"},
        )

        self.assertTrue(info.is_live)
        self.assertEqual(info.primary_url, "https://example.com/live.m3u8")
        self.assertEqual(info.headers["referer"], "https://live.douyin.com")

    def test_enums_use_existing_config_values(self):
        self.assertEqual(Quality.ORIGINAL.value, "原画")
        self.assertEqual(OutputFormat.TS.value, "ts")
        self.assertEqual(RecordingStatus.IDLE.value, "idle")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.core.test_models -v
```

Expected: failure with `ModuleNotFoundError: No module named 'douyinliverecorder'`.

- [ ] **Step 3: Add package and models**

Create `douyinliverecorder/__init__.py`:

```python
"""Refactored application package for DouyinLiveRecorder."""

__version__ = "4.0.7"
```

Create `douyinliverecorder/core/__init__.py`:

```python
"""Core recording models and services."""
```

Create `douyinliverecorder/core/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping
from uuid import uuid4


class Quality(str, Enum):
    ORIGINAL = "原画"
    BLUE_RAY = "蓝光"
    ULTRA = "超清"
    HIGH = "高清"
    STANDARD = "标清"
    SMOOTH = "流畅"


class OutputFormat(str, Enum):
    TS = "ts"
    MKV = "mkv"
    FLV = "flv"
    MP4 = "mp4"
    MP3 = "mp3"
    M4A = "m4a"


class RecordingStatus(str, Enum):
    IDLE = "idle"
    RESOLVING = "resolving"
    LIVE = "live"
    RECORDING = "recording"
    STOPPING = "stopping"
    ERROR = "error"


def normalize_url(url: str) -> str:
    normalized = url.strip()
    if normalized and "://" not in normalized:
        normalized = f"https://{normalized}"
    return normalized


@dataclass(slots=True)
class RecordingTarget:
    url: str
    id: str = field(default_factory=lambda: uuid4().hex)
    display_name: str = ""
    quality: Quality = Quality.ORIGINAL
    enabled: bool = True
    platform_key: str = ""

    def __post_init__(self) -> None:
        self.url = normalize_url(self.url)


@dataclass(frozen=True, slots=True)
class StreamInfo:
    platform_key: str
    platform_name: str
    anchor_name: str = ""
    title: str = ""
    is_live: bool = False
    quality: Quality = Quality.ORIGINAL
    primary_url: str = ""
    flv_url: str = ""
    hls_url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RecordingTask:
    target: RecordingTarget
    status: RecordingStatus = RecordingStatus.IDLE
    stream: StreamInfo | None = None
    output_path: Path | None = None
    started_at: float | None = None
    last_error: str = ""
    retry_count: int = 0
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.core.test_models -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/__init__.py douyinliverecorder/core/__init__.py douyinliverecorder/core/models.py tests/core/test_models.py
git commit -m "feat: add core recording models"
```

---

### Task 2: Core Event Bus

**Files:**
- Create: `douyinliverecorder/core/events.py`
- Create: `tests/core/test_events.py`

- [ ] **Step 1: Write event bus tests**

Create `tests/core/test_events.py`:

```python
import unittest

from douyinliverecorder.core.events import EventBus, RecorderEvent, RecorderEventType


class EventBusTests(unittest.TestCase):
    def test_publish_sends_event_to_subscribers(self):
        bus = EventBus()
        received = []

        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.TARGET_ADDED, target_id="abc", message="added")
        bus.publish(event)

        self.assertEqual(received, [event])

    def test_unsubscribe_stops_delivery(self):
        bus = EventBus()
        received = []

        bus.subscribe(received.append)
        bus.unsubscribe(received.append)
        bus.publish(RecorderEvent(type=RecorderEventType.ERROR, target_id="abc", message="failed"))

        self.assertEqual(received, [])

    def test_subscriber_error_does_not_stop_other_subscribers(self):
        bus = EventBus()
        received = []

        def broken(_event):
            raise RuntimeError("subscriber failed")

        bus.subscribe(broken)
        bus.subscribe(received.append)
        event = RecorderEvent(type=RecorderEventType.RECORDING_STARTED, target_id="abc")
        bus.publish(event)

        self.assertEqual(received, [event])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.core.test_events -v
```

Expected: failure with `ModuleNotFoundError` or `ImportError` for `douyinliverecorder.core.events`.

- [ ] **Step 3: Implement event bus**

Create `douyinliverecorder/core/events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Callable


class RecorderEventType(str, Enum):
    TARGET_ADDED = "target_added"
    TARGET_UPDATED = "target_updated"
    TARGET_REMOVED = "target_removed"
    RESOLVE_STARTED = "resolve_started"
    LIVE_DETECTED = "live_detected"
    OFFLINE_DETECTED = "offline_detected"
    RECORDING_STARTED = "recording_started"
    RECORDING_STOPPED = "recording_stopped"
    RECORDING_FAILED = "recording_failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RecorderEvent:
    type: RecorderEventType
    target_id: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time)


Subscriber = Callable[[RecorderEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers = [item for item in self._subscribers if item != subscriber]

    def publish(self, event: RecorderEvent) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:
                continue
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.core.test_events -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/core/events.py tests/core/test_events.py
git commit -m "feat: add recorder event bus"
```

---

### Task 3: Config Service

**Files:**
- Create: `douyinliverecorder/core/config.py`
- Create: `tests/core/test_config.py`

- [ ] **Step 1: Write config tests**

Create `tests/core/test_config.py`:

```python
import tempfile
import unittest
from pathlib import Path

from douyinliverecorder.core.config import AppConfig, ConfigService
from douyinliverecorder.core.models import OutputFormat, Quality


class ConfigServiceTests(unittest.TestCase):
    def test_loads_existing_chinese_config_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            path.write_text(
                "[录制设置]\n"
                "直播保存路径(不填则默认) = D:/videos\n"
                "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频 = mp4\n"
                "原画|超清|高清|标清|流畅 = 高清\n"
                "循环时间(秒) = 60\n"
                "同一时间访问网络的线程数 = 2\n"
                "是否使用代理ip(是/否) = 是\n"
                "代理地址 = 127.0.0.1:7890\n",
                encoding="utf-8-sig",
            )

            config = ConfigService(path).load()

            self.assertEqual(config.save_path, "D:/videos")
            self.assertEqual(config.output_format, OutputFormat.MP4)
            self.assertEqual(config.quality, Quality.HIGH)
            self.assertEqual(config.loop_seconds, 60)
            self.assertEqual(config.max_concurrency, 2)
            self.assertTrue(config.use_proxy)
            self.assertEqual(config.proxy_addr, "127.0.0.1:7890")

    def test_missing_file_returns_defaults_and_creates_file_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)

            config = service.load()
            service.save(config)

            self.assertEqual(config.output_format, OutputFormat.TS)
            self.assertEqual(config.quality, Quality.ORIGINAL)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8-sig")
            self.assertIn("录制设置", content)
            self.assertIn("循环时间(秒)", content)

    def test_save_updates_known_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.ini"
            service = ConfigService(path)
            service.save(AppConfig(save_path="E:/recordings", loop_seconds=30, use_proxy=True, proxy_addr="host:8080"))

            reloaded = service.load()

            self.assertEqual(reloaded.save_path, "E:/recordings")
            self.assertEqual(reloaded.loop_seconds, 30)
            self.assertTrue(reloaded.use_proxy)
            self.assertEqual(reloaded.proxy_addr, "host:8080")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.core.test_config -v
```

Expected: failure importing `douyinliverecorder.core.config`.

- [ ] **Step 3: Implement config service**

Create `douyinliverecorder/core/config.py`:

```python
from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from .models import OutputFormat, Quality


RECORDING_SECTION = "录制设置"


@dataclass(slots=True)
class AppConfig:
    save_path: str = ""
    output_format: OutputFormat = OutputFormat.TS
    quality: Quality = Quality.ORIGINAL
    loop_seconds: int = 300
    max_concurrency: int = 3
    use_proxy: bool = False
    proxy_addr: str = ""
    split_enabled: bool = True
    split_seconds: int = 1800
    convert_to_mp4: bool = True


class ConfigService:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AppConfig:
        parser = self._read()
        return AppConfig(
            save_path=self._get(parser, "直播保存路径(不填则默认)", ""),
            output_format=self._format(self._get(parser, "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频", "ts")),
            quality=self._quality(self._get(parser, "原画|超清|高清|标清|流畅", "原画")),
            loop_seconds=self._int(self._get(parser, "循环时间(秒)", "300"), 300),
            max_concurrency=self._int(self._get(parser, "同一时间访问网络的线程数", "3"), 3),
            use_proxy=self._bool(self._get(parser, "是否使用代理ip(是/否)", "否")),
            proxy_addr=self._get(parser, "代理地址", ""),
            split_enabled=self._bool(self._get(parser, "分段录制是否开启", "是")),
            split_seconds=self._int(self._get(parser, "视频分段时间(秒)", "1800"), 1800),
            convert_to_mp4=self._bool(self._get(parser, "录制完成后自动转为mp4格式", "是")),
        )

    def save(self, config: AppConfig) -> None:
        parser = self._read()
        if not parser.has_section(RECORDING_SECTION):
            parser.add_section(RECORDING_SECTION)
        values = {
            "直播保存路径(不填则默认)": config.save_path,
            "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频": config.output_format.value,
            "原画|超清|高清|标清|流畅": config.quality.value,
            "循环时间(秒)": str(config.loop_seconds),
            "同一时间访问网络的线程数": str(config.max_concurrency),
            "是否使用代理ip(是/否)": "是" if config.use_proxy else "否",
            "代理地址": config.proxy_addr,
            "分段录制是否开启": "是" if config.split_enabled else "否",
            "视频分段时间(秒)": str(config.split_seconds),
            "录制完成后自动转为mp4格式": "是" if config.convert_to_mp4 else "否",
        }
        for key, value in values.items():
            parser.set(RECORDING_SECTION, key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8-sig") as file:
            parser.write(file)

    def _read(self) -> configparser.RawConfigParser:
        parser = configparser.RawConfigParser()
        if self.path.exists():
            parser.read(self.path, encoding="utf-8-sig")
        if not parser.has_section(RECORDING_SECTION):
            parser.add_section(RECORDING_SECTION)
        return parser

    @staticmethod
    def _get(parser: configparser.RawConfigParser, key: str, default: str) -> str:
        try:
            return parser.get(RECORDING_SECTION, key)
        except (configparser.NoOptionError, configparser.NoSectionError):
            return default

    @staticmethod
    def _bool(value: str) -> bool:
        return value.strip() == "是"

    @staticmethod
    def _int(value: str, default: int) -> int:
        try:
            return int(str(value).strip())
        except ValueError:
            return default

    @staticmethod
    def _format(value: str) -> OutputFormat:
        normalized = value.strip().lower().replace("音频", "")
        for item in OutputFormat:
            if normalized == item.value:
                return item
        return OutputFormat.TS

    @staticmethod
    def _quality(value: str) -> Quality:
        for item in Quality:
            if value.strip() == item.value:
                return item
        return Quality.ORIGINAL
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.core.test_config -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/core/config.py tests/core/test_config.py
git commit -m "feat: add typed config service"
```

---

### Task 4: URL Store

**Files:**
- Create: `douyinliverecorder/core/url_store.py`
- Create: `tests/core/test_url_store.py`

- [ ] **Step 1: Write URL store tests**

Create `tests/core/test_url_store.py`:

```python
import tempfile
import unittest
from pathlib import Path

from douyinliverecorder.core.models import Quality
from douyinliverecorder.core.url_store import UrlStore


class UrlStoreTests(unittest.TestCase):
    def test_loads_plain_quality_and_name_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            path.write_text(
                "https://live.douyin.com/111\n"
                "高清,https://live.douyin.com/222\n"
                "原画,https://live.douyin.com/333,主播三\n"
                "#https://live.douyin.com/444\n",
                encoding="utf-8-sig",
            )

            targets = UrlStore(path).load()

            self.assertEqual(len(targets), 4)
            self.assertEqual(targets[0].quality, Quality.ORIGINAL)
            self.assertEqual(targets[1].quality, Quality.HIGH)
            self.assertEqual(targets[2].display_name, "主播三")
            self.assertFalse(targets[3].enabled)

    def test_save_round_trips_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            store = UrlStore(path)
            targets = store.load_from_lines(["高清,https://live.douyin.com/222,主播二"])

            store.save(targets)
            reloaded = store.load()

            self.assertEqual(len(reloaded), 1)
            self.assertEqual(reloaded[0].url, "https://live.douyin.com/222")
            self.assertEqual(reloaded[0].quality, Quality.HIGH)
            self.assertEqual(reloaded[0].display_name, "主播二")

    def test_add_skips_duplicate_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "URL_config.ini"
            store = UrlStore(path)
            first = store.load_from_lines(["https://live.douyin.com/111"])
            second = store.add(first, "live.douyin.com/111")

            self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.core.test_url_store -v
```

Expected: failure importing `douyinliverecorder.core.url_store`.

- [ ] **Step 3: Implement URL store**

Create `douyinliverecorder/core/url_store.py`:

```python
from __future__ import annotations

from pathlib import Path

from .models import Quality, RecordingTarget, normalize_url


QUALITY_BY_VALUE = {item.value: item for item in Quality}


class UrlStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[RecordingTarget]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        return self.load_from_lines(lines)

    def load_from_lines(self, lines: list[str]) -> list[RecordingTarget]:
        targets: list[RecordingTarget] = []
        seen: set[str] = set()
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            enabled = not line.startswith("#")
            if not enabled:
                line = line.lstrip("#").strip()
            if not line:
                continue
            target = self._parse_line(line, enabled)
            if target.url in seen:
                continue
            seen.add(target.url)
            targets.append(target)
        return targets

    def save(self, targets: list[RecordingTarget]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [self._format_target(target) for target in targets]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8-sig")

    def add(self, targets: list[RecordingTarget], url: str, quality: Quality = Quality.ORIGINAL, name: str = "") -> list[RecordingTarget]:
        normalized = normalize_url(url)
        if any(target.url == normalized for target in targets):
            return list(targets)
        return [*targets, RecordingTarget(url=normalized, quality=quality, display_name=name)]

    def _parse_line(self, line: str, enabled: bool) -> RecordingTarget:
        parts = [part.strip() for part in line.replace("，", ",").split(",")]
        quality = Quality.ORIGINAL
        url = ""
        name = ""
        if len(parts) == 1:
            url = parts[0]
        elif parts[0] in QUALITY_BY_VALUE:
            quality = QUALITY_BY_VALUE[parts[0]]
            url = parts[1]
            name = parts[2] if len(parts) > 2 else ""
        else:
            url = parts[0]
            name = parts[1] if len(parts) > 1 else ""
        return RecordingTarget(url=url, quality=quality, display_name=name, enabled=enabled)

    def _format_target(self, target: RecordingTarget) -> str:
        prefix = "" if target.enabled else "#"
        parts = [target.quality.value, target.url]
        if target.display_name:
            parts.append(target.display_name)
        return prefix + ",".join(parts)
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.core.test_url_store -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/core/url_store.py tests/core/test_url_store.py
git commit -m "feat: add URL target store"
```

---

### Task 5: Platform Adapter Registry

**Files:**
- Create: `douyinliverecorder/platforms/__init__.py`
- Create: `douyinliverecorder/platforms/base.py`
- Create: `douyinliverecorder/platforms/registry.py`
- Create: `tests/platforms/test_registry.py`

- [ ] **Step 1: Write registry tests**

Create `tests/platforms/test_registry.py`:

```python
import unittest

from douyinliverecorder.core.models import RecordingTarget, StreamInfo
from douyinliverecorder.platforms.base import PlatformAdapter, ResolveContext
from douyinliverecorder.platforms.registry import PlatformRegistry


class FakeAdapter:
    key = "fake"
    display_name = "Fake"

    def matches(self, url: str) -> bool:
        return "example.com" in url

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        return StreamInfo(platform_key=self.key, platform_name=self.display_name, anchor_name="fake")


class RegistryTests(unittest.TestCase):
    def test_returns_matching_adapter(self):
        registry = PlatformRegistry([FakeAdapter()])
        adapter = registry.match("https://example.com/live")

        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.key, "fake")

    def test_returns_none_for_unknown_url(self):
        registry = PlatformRegistry([FakeAdapter()])

        self.assertIsNone(registry.match("https://unknown.invalid/live"))

    def test_protocol_shape_accepts_fake_adapter(self):
        adapter: PlatformAdapter = FakeAdapter()

        self.assertTrue(adapter.matches("https://example.com/live"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.platforms.test_registry -v
```

Expected: failure importing `douyinliverecorder.platforms`.

- [ ] **Step 3: Implement adapter base and registry**

Create `douyinliverecorder/platforms/__init__.py`:

```python
"""Platform adapters for live stream resolution."""
```

Create `douyinliverecorder/platforms/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from douyinliverecorder.core.models import Quality, RecordingTarget, StreamInfo


@dataclass(frozen=True, slots=True)
class ResolveContext:
    quality: Quality = Quality.ORIGINAL
    proxy_addr: str = ""
    cookies: dict[str, str] | None = None

    def cookie_value(self, key: str) -> str:
        if not self.cookies:
            return ""
        return self.cookies.get(key, "")


class PlatformAdapter(Protocol):
    key: str
    display_name: str

    def matches(self, url: str) -> bool:
        raise NotImplementedError

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        raise NotImplementedError
```

Create `douyinliverecorder/platforms/registry.py`:

```python
from __future__ import annotations

from .base import PlatformAdapter


class PlatformRegistry:
    def __init__(self, adapters: list[PlatformAdapter] | None = None) -> None:
        self._adapters = adapters or []

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters.append(adapter)

    def match(self, url: str) -> PlatformAdapter | None:
        for adapter in self._adapters:
            if adapter.matches(url):
                return adapter
        return None

    @property
    def adapters(self) -> tuple[PlatformAdapter, ...]:
        return tuple(self._adapters)
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.platforms.test_registry -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/platforms/__init__.py douyinliverecorder/platforms/base.py douyinliverecorder/platforms/registry.py tests/platforms/test_registry.py
git commit -m "feat: add platform adapter registry"
```

---

### Task 6: Douyin Platform Adapter

**Files:**
- Create: `douyinliverecorder/platforms/douyin.py`
- Create: `tests/platforms/test_douyin_adapter.py`

- [ ] **Step 1: Write Douyin adapter tests**

Create `tests/platforms/test_douyin_adapter.py`:

```python
import unittest

from douyinliverecorder.core.models import Quality, RecordingTarget
from douyinliverecorder.platforms.base import ResolveContext
from douyinliverecorder.platforms.douyin import DouyinAdapter


async def fake_web_fetch(url: str, proxy_addr: str, cookies: str):
    return {"status": 2, "anchor_name": "主播A", "title": "直播标题", "source": "web"}


async def fake_app_fetch(url: str, proxy_addr: str, cookies: str):
    return {"status": 2, "anchor_name": "主播B", "title": "直播标题", "source": "app"}


async def fake_stream_resolve(json_data: dict, video_quality: str, proxy_addr: str):
    return {
        "anchor_name": json_data["anchor_name"],
        "is_live": True,
        "title": json_data["title"],
        "quality": video_quality,
        "record_url": "https://pull.example/live.m3u8",
        "flv_url": "https://pull.example/live.flv",
        "m3u8_url": "https://pull.example/live.m3u8",
    }


class DouyinAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_matches_supported_hosts(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)

        self.assertTrue(adapter.matches("https://live.douyin.com/123"))
        self.assertTrue(adapter.matches("https://v.douyin.com/abc"))
        self.assertTrue(adapter.matches("https://www.douyin.com/user/example"))
        self.assertFalse(adapter.matches("https://live.bilibili.com/1"))

    async def test_resolves_web_live_room(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)
        target = RecordingTarget(url="https://live.douyin.com/123", quality=Quality.HIGH)

        info = await adapter.resolve(target, ResolveContext(quality=Quality.HIGH, proxy_addr="127.0.0.1:7890", cookies={"douyin": "cookie"}))

        self.assertTrue(info.is_live)
        self.assertEqual(info.platform_key, "douyin")
        self.assertEqual(info.anchor_name, "主播A")
        self.assertEqual(info.primary_url, "https://pull.example/live.m3u8")
        self.assertEqual(info.flv_url, "https://pull.example/live.flv")

    async def test_resolves_share_link_with_app_fetcher(self):
        adapter = DouyinAdapter(fake_web_fetch, fake_app_fetch, fake_stream_resolve)
        target = RecordingTarget(url="https://v.douyin.com/abc")

        info = await adapter.resolve(target, ResolveContext())

        self.assertEqual(info.anchor_name, "主播B")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.platforms.test_douyin_adapter -v
```

Expected: failure importing `douyinliverecorder.platforms.douyin`.

- [ ] **Step 3: Implement Douyin adapter**

Create `douyinliverecorder/platforms/douyin.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable

from douyinliverecorder.core.models import Quality, RecordingTarget, StreamInfo
from douyinliverecorder.platforms.base import ResolveContext
from src import spider, stream


FetchFn = Callable[[str, str, str], Awaitable[dict]]
StreamFn = Callable[[dict, str, str], Awaitable[dict]]


QUALITY_CODES = {
    Quality.ORIGINAL: "OD",
    Quality.BLUE_RAY: "BD",
    Quality.ULTRA: "UHD",
    Quality.HIGH: "HD",
    Quality.STANDARD: "SD",
    Quality.SMOOTH: "LD",
}


class DouyinAdapter:
    key = "douyin"
    display_name = "Douyin"

    def __init__(
        self,
        web_fetcher: FetchFn = spider.get_douyin_web_stream_data,
        app_fetcher: FetchFn = spider.get_douyin_app_stream_data,
        stream_resolver: StreamFn = stream.get_douyin_stream_url,
    ) -> None:
        self._web_fetcher = web_fetcher
        self._app_fetcher = app_fetcher
        self._stream_resolver = stream_resolver

    def matches(self, url: str) -> bool:
        return "live.douyin.com/" in url or "v.douyin.com/" in url or "www.douyin.com/" in url

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        cookies = context.cookie_value("douyin")
        proxy = context.proxy_addr
        if "v.douyin.com" in target.url or "/user/" in target.url:
            data = await self._app_fetcher(target.url, proxy, cookies)
        else:
            data = await self._web_fetcher(target.url, proxy, cookies)
        raw = await self._stream_resolver(data, QUALITY_CODES.get(context.quality, "OD"), proxy)
        return StreamInfo(
            platform_key=self.key,
            platform_name=self.display_name,
            anchor_name=raw.get("anchor_name") or "",
            title=raw.get("title") or "",
            is_live=bool(raw.get("is_live")),
            quality=context.quality,
            primary_url=raw.get("record_url") or "",
            flv_url=raw.get("flv_url") or "",
            hls_url=raw.get("m3u8_url") or raw.get("record_url") or "",
            headers={"referer": "https://live.douyin.com/"},
        )
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.platforms.test_douyin_adapter -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/platforms/douyin.py tests/platforms/test_douyin_adapter.py
git commit -m "feat: add Douyin platform adapter"
```

---

### Task 7: FFmpeg Recorder Wrapper

**Files:**
- Create: `douyinliverecorder/recorders/__init__.py`
- Create: `douyinliverecorder/recorders/ffmpeg.py`
- Create: `tests/recorders/test_ffmpeg.py`

- [ ] **Step 1: Write FFmpeg tests**

Create `tests/recorders/test_ffmpeg.py`:

```python
import unittest
from pathlib import Path

from douyinliverecorder.core.models import OutputFormat, RecordingTarget, StreamInfo
from douyinliverecorder.recorders.ffmpeg import FFmpegRecorder, RecorderOptions


class FFmpegRecorderTests(unittest.TestCase):
    def test_builds_segmented_ts_command(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123", display_name="主播A")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")

        command = recorder.build_command(target, stream, Path("D:/downloads"), RecorderOptions(output_format=OutputFormat.TS, split_enabled=True, split_seconds=1800))

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-segment_time", command)
        self.assertIn("1800", command)
        self.assertIn("https://pull.example/live.m3u8", command)
        self.assertTrue(command[-1].endswith("_%03d.ts"))

    def test_builds_mp4_command_without_segments(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")

        command = recorder.build_command(target, stream, Path("D:/downloads"), RecorderOptions(output_format=OutputFormat.MP4, split_enabled=False))

        self.assertIn("-f", command)
        self.assertIn("mp4", command)
        self.assertTrue(command[-1].endswith(".mp4"))

    def test_rejects_not_live_stream(self):
        recorder = FFmpegRecorder(ffmpeg_path="ffmpeg")
        target = RecordingTarget(url="https://live.douyin.com/123")
        stream = StreamInfo(platform_key="douyin", platform_name="Douyin")

        with self.assertRaises(ValueError):
            recorder.build_command(target, stream, Path("D:/downloads"), RecorderOptions())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.recorders.test_ffmpeg -v
```

Expected: failure importing `douyinliverecorder.recorders.ffmpeg`.

- [ ] **Step 3: Implement FFmpeg recorder wrapper**

Create `douyinliverecorder/recorders/__init__.py`:

```python
"""Recording process wrappers."""
```

Create `douyinliverecorder/recorders/ffmpeg.py`:

```python
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from douyinliverecorder.core.models import OutputFormat, RecordingTarget, StreamInfo


@dataclass(frozen=True, slots=True)
class RecorderOptions:
    output_format: OutputFormat = OutputFormat.TS
    split_enabled: bool = True
    split_seconds: int = 1800


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[\/\\:*?"<>|&#.\s]+', "_", value.strip())
    return cleaned.strip("_") or "live"


class FFmpegRecorder:
    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    def build_command(self, target: RecordingTarget, stream: StreamInfo, output_dir: Path, options: RecorderOptions) -> list[str]:
        if not stream.is_live or not stream.primary_url:
            raise ValueError("stream is not live or has no recording URL")
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = self._stem(target, stream)
        input_args = [self.ffmpeg_path, "-y", "-headers", self._headers(stream), "-i", stream.primary_url]
        if options.split_enabled:
            output_path = output_dir / f"{stem}_%03d.{options.output_format.value}"
            return [
                *input_args,
                "-c:v",
                "copy",
                "-c:a",
                "copy" if options.output_format != OutputFormat.MP4 else "aac",
                "-map",
                "0",
                "-f",
                "segment",
                "-segment_time",
                str(options.split_seconds),
                "-segment_format",
                "mpegts" if options.output_format == OutputFormat.TS else options.output_format.value,
                "-reset_timestamps",
                "1",
                str(output_path),
            ]
        output_path = output_dir / f"{stem}.{options.output_format.value}"
        muxer = "mpegts" if options.output_format == OutputFormat.TS else options.output_format.value
        return [*input_args, "-c:v", "copy", "-c:a", "copy", "-map", "0", "-f", muxer, str(output_path)]

    def start(self, command: list[str]) -> subprocess.Popen:
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def stop(self, process: subprocess.Popen, timeout: int = 10) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()

    def _stem(self, target: RecordingTarget, stream: StreamInfo) -> str:
        anchor = target.display_name or stream.anchor_name or "live"
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        return f"{safe_name(anchor)}_{timestamp}"

    def _headers(self, stream: StreamInfo) -> str:
        return "".join(f"{key}: {value}\r\n" for key, value in stream.headers.items())
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.recorders.test_ffmpeg -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/recorders/__init__.py douyinliverecorder/recorders/ffmpeg.py tests/recorders/test_ffmpeg.py
git commit -m "feat: add FFmpeg recorder wrapper"
```

---

### Task 8: Scheduler Vertical Slice

**Files:**
- Create: `douyinliverecorder/core/scheduler.py`
- Create: `tests/core/test_scheduler.py`

- [ ] **Step 1: Write scheduler tests**

Create `tests/core/test_scheduler.py`:

```python
import unittest
from pathlib import Path

from douyinliverecorder.core.events import EventBus, RecorderEventType
from douyinliverecorder.core.models import Quality, RecordingTarget, StreamInfo
from douyinliverecorder.core.scheduler import RecordingScheduler, SchedulerConfig
from douyinliverecorder.platforms.base import ResolveContext
from douyinliverecorder.platforms.registry import PlatformRegistry


class FakeAdapter:
    key = "douyin"
    display_name = "Douyin"

    def matches(self, url: str) -> bool:
        return "douyin.com" in url

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        return StreamInfo(platform_key="douyin", platform_name="Douyin", anchor_name="主播A", is_live=True, primary_url="https://pull.example/live.m3u8")


class FakeProcess:
    def __init__(self):
        self.stopped = False


class FakeRecorder:
    def __init__(self):
        self.commands = []
        self.process = FakeProcess()

    def build_command(self, target, stream, output_dir, options):
        return ["ffmpeg", "-i", stream.primary_url, str(Path(output_dir) / "out.ts")]

    def start(self, command):
        self.commands.append(command)
        return self.process

    def stop(self, process, timeout=10):
        process.stopped = True


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_once_starts_recording_for_live_target(self):
        bus = EventBus()
        events = []
        bus.subscribe(events.append)
        recorder = FakeRecorder()
        scheduler = RecordingScheduler(
            registry=PlatformRegistry([FakeAdapter()]),
            recorder=recorder,
            event_bus=bus,
            config=SchedulerConfig(output_dir=Path("downloads"), quality=Quality.ORIGINAL),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])

        self.assertEqual(len(recorder.commands), 1)
        self.assertIn(RecorderEventType.RECORDING_STARTED, [event.type for event in events])

    async def test_stop_target_stops_running_process(self):
        recorder = FakeRecorder()
        scheduler = RecordingScheduler(
            registry=PlatformRegistry([FakeAdapter()]),
            recorder=recorder,
            event_bus=EventBus(),
            config=SchedulerConfig(output_dir=Path("downloads"), quality=Quality.ORIGINAL),
        )
        target = RecordingTarget(url="https://live.douyin.com/123")

        await scheduler.check_once([target])
        scheduler.stop_target(target.id)

        self.assertTrue(recorder.process.stopped)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.core.test_scheduler -v
```

Expected: failure importing `douyinliverecorder.core.scheduler`.

- [ ] **Step 3: Implement scheduler**

Create `douyinliverecorder/core/scheduler.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from douyinliverecorder.core.events import EventBus, RecorderEvent, RecorderEventType
from douyinliverecorder.core.models import OutputFormat, Quality, RecordingStatus, RecordingTarget, RecordingTask
from douyinliverecorder.platforms.base import ResolveContext
from douyinliverecorder.platforms.registry import PlatformRegistry
from douyinliverecorder.recorders.ffmpeg import RecorderOptions


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    output_dir: Path
    quality: Quality
    proxy_addr: str = ""
    cookies: dict[str, str] | None = None
    output_format: OutputFormat = OutputFormat.TS
    split_enabled: bool = True
    split_seconds: int = 1800


class RecordingScheduler:
    def __init__(self, registry: PlatformRegistry, recorder: Any, event_bus: EventBus, config: SchedulerConfig) -> None:
        self.registry = registry
        self.recorder = recorder
        self.event_bus = event_bus
        self.config = config
        self._tasks: dict[str, RecordingTask] = {}
        self._processes: dict[str, Any] = {}

    async def check_once(self, targets: list[RecordingTarget]) -> None:
        for target in targets:
            if not target.enabled or target.id in self._processes:
                continue
            await self._check_target(target)

    def stop_target(self, target_id: str) -> None:
        process = self._processes.pop(target_id, None)
        if process is None:
            return
        self.recorder.stop(process)
        task = self._tasks.get(target_id)
        if task:
            task.status = RecordingStatus.IDLE
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.RECORDING_STOPPED, target_id=target_id))

    def stop_all(self) -> None:
        for target_id in list(self._processes):
            self.stop_target(target_id)

    @property
    def tasks(self) -> dict[str, RecordingTask]:
        return dict(self._tasks)

    async def _check_target(self, target: RecordingTarget) -> None:
        adapter = self.registry.match(target.url)
        if adapter is None:
            self.event_bus.publish(RecorderEvent(type=RecorderEventType.ERROR, target_id=target.id, message="unsupported URL"))
            return
        task = self._tasks.setdefault(target.id, RecordingTask(target=target))
        task.status = RecordingStatus.RESOLVING
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.RESOLVE_STARTED, target_id=target.id))
        context = ResolveContext(quality=target.quality or self.config.quality, proxy_addr=self.config.proxy_addr, cookies=self.config.cookies)
        stream = await adapter.resolve(target, context)
        task.stream = stream
        if not stream.is_live:
            task.status = RecordingStatus.IDLE
            self.event_bus.publish(RecorderEvent(type=RecorderEventType.OFFLINE_DETECTED, target_id=target.id))
            return
        task.status = RecordingStatus.LIVE
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.LIVE_DETECTED, target_id=target.id, payload={"anchor": stream.anchor_name}))
        options = RecorderOptions(output_format=self.config.output_format, split_enabled=self.config.split_enabled, split_seconds=self.config.split_seconds)
        command = self.recorder.build_command(target, stream, self.config.output_dir, options)
        process = self.recorder.start(command)
        self._processes[target.id] = process
        task.status = RecordingStatus.RECORDING
        self.event_bus.publish(RecorderEvent(type=RecorderEventType.RECORDING_STARTED, target_id=target.id, payload={"command": command}))
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.core.test_scheduler -v
```

Expected: all 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/core/scheduler.py tests/core/test_scheduler.py
git commit -m "feat: add recording scheduler slice"
```

---

### Task 9: Desktop Controller

**Files:**
- Create: `douyinliverecorder/apps/__init__.py`
- Create: `douyinliverecorder/apps/desktop/__init__.py`
- Create: `douyinliverecorder/apps/desktop/controller.py`
- Create: `tests/apps/desktop/test_controller.py`

- [ ] **Step 1: Write controller tests**

Create `tests/apps/desktop/test_controller.py`:

```python
import tempfile
import unittest
from pathlib import Path

from douyinliverecorder.apps.desktop.controller import DesktopController
from douyinliverecorder.core.config import AppConfig
from douyinliverecorder.core.models import Quality


class FakeConfigService:
    def __init__(self):
        self.saved = None

    def load(self):
        return AppConfig(loop_seconds=1)

    def save(self, config):
        self.saved = config


class FakeScheduler:
    def __init__(self):
        self.checked = []
        self.stopped = False

    async def check_once(self, targets):
        self.checked.append(list(targets))

    def stop_all(self):
        self.stopped = True


class DesktopControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_target_persists_url_and_runs_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            url_file = Path(tmp) / "URL_config.ini"
            scheduler = FakeScheduler()
            controller = DesktopController(FakeConfigService(), url_file, scheduler)

            controller.add_target("live.douyin.com/123", Quality.HIGH, "主播")
            await controller.check_once()

            self.assertEqual(len(controller.targets), 1)
            self.assertEqual(controller.targets[0].url, "https://live.douyin.com/123")
            self.assertEqual(controller.targets[0].display_name, "主播")
            self.assertEqual(len(scheduler.checked), 1)
            self.assertTrue(url_file.exists())

    async def test_stop_calls_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = FakeScheduler()
            controller = DesktopController(FakeConfigService(), Path(tmp) / "URL_config.ini", scheduler)

            controller.stop_all()

            self.assertTrue(scheduler.stopped)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest tests.apps.desktop.test_controller -v
```

Expected: failure importing `douyinliverecorder.apps.desktop.controller`.

- [ ] **Step 3: Implement desktop controller**

Create `douyinliverecorder/apps/__init__.py`:

```python
"""Application entry points."""
```

Create `douyinliverecorder/apps/desktop/__init__.py`:

```python
"""Desktop GUI application."""
```

Create `douyinliverecorder/apps/desktop/controller.py`:

```python
from __future__ import annotations

from pathlib import Path

from douyinliverecorder.core.config import AppConfig, ConfigService
from douyinliverecorder.core.models import Quality, RecordingTarget
from douyinliverecorder.core.url_store import UrlStore


class DesktopController:
    def __init__(self, config_service: ConfigService, url_file: str | Path, scheduler) -> None:
        self.config_service = config_service
        self.url_store = UrlStore(url_file)
        self.scheduler = scheduler
        self.config: AppConfig = self.config_service.load()
        self.targets: list[RecordingTarget] = self.url_store.load()

    def add_target(self, url: str, quality: Quality = Quality.ORIGINAL, name: str = "") -> None:
        self.targets = self.url_store.add(self.targets, url, quality, name)
        self.url_store.save(self.targets)

    def remove_target(self, target_id: str) -> None:
        self.targets = [target for target in self.targets if target.id != target_id]
        self.url_store.save(self.targets)

    def set_target_enabled(self, target_id: str, enabled: bool) -> None:
        for target in self.targets:
            if target.id == target_id:
                target.enabled = enabled
        self.url_store.save(self.targets)

    async def check_once(self) -> None:
        await self.scheduler.check_once(self.targets)

    def stop_all(self) -> None:
        self.scheduler.stop_all()
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```powershell
python -m unittest tests.apps.desktop.test_controller -v
```

Expected: all 2 tests pass.

- [ ] **Step 5: Commit**

```powershell
git add douyinliverecorder/apps/__init__.py douyinliverecorder/apps/desktop/__init__.py douyinliverecorder/apps/desktop/controller.py tests/apps/desktop/test_controller.py
git commit -m "feat: add desktop controller"
```

---

### Task 10: Minimal Kivy Desktop App

**Files:**
- Create: `douyinliverecorder/apps/desktop/main.py`
- Create: `requirements-gui.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add GUI dependency file**

Create `requirements-gui.txt`:

```text
-r requirements.txt
kivy>=2.3.0
pyinstaller>=6.0.0
```

- [ ] **Step 2: Add optional GUI metadata**

Modify `pyproject.toml` by adding this section after `[project.urls]`:

```toml
[project.optional-dependencies]
gui = [
    "kivy>=2.3.0",
    "pyinstaller>=6.0.0"
]
```

- [ ] **Step 3: Create Kivy entry point**

Create `douyinliverecorder/apps/desktop/main.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from douyinliverecorder.apps.desktop.controller import DesktopController
from douyinliverecorder.core.config import ConfigService
from douyinliverecorder.core.events import EventBus
from douyinliverecorder.core.models import Quality
from douyinliverecorder.core.scheduler import RecordingScheduler, SchedulerConfig
from douyinliverecorder.platforms.douyin import DouyinAdapter
from douyinliverecorder.platforms.registry import PlatformRegistry
from douyinliverecorder.recorders.ffmpeg import FFmpegRecorder


class DesktopRoot(BoxLayout):
    def __init__(self, controller: DesktopController, event_bus: EventBus, **kwargs):
        super().__init__(orientation="vertical", spacing=8, padding=8, **kwargs)
        self.controller = controller
        self.url_input = TextInput(hint_text="输入抖音直播间地址", multiline=False, size_hint_y=None, height=42)
        self.name_input = TextInput(hint_text="主播备注，可不填", multiline=False, size_hint_y=None, height=42)
        self.status = Label(text="未开始值守", size_hint_y=None, height=34)
        self.log = Label(text="", valign="top", halign="left", size_hint_y=None)
        self.log.bind(texture_size=self._resize_log)
        event_bus.subscribe(self._on_event)

        self.add_widget(self.url_input)
        self.add_widget(self.name_input)

        row = BoxLayout(size_hint_y=None, height=44, spacing=8)
        add_btn = Button(text="添加直播间")
        add_btn.bind(on_press=self._add_target)
        check_btn = Button(text="检测并录制")
        check_btn.bind(on_press=self._check_once)
        stop_btn = Button(text="停止全部")
        stop_btn.bind(on_press=self._stop_all)
        row.add_widget(add_btn)
        row.add_widget(check_btn)
        row.add_widget(stop_btn)
        self.add_widget(row)

        self.add_widget(self.status)
        scroll = ScrollView()
        scroll.add_widget(self.log)
        self.add_widget(scroll)
        self._render_targets()

    def _resize_log(self, *_args):
        self.log.height = self.log.texture_size[1]
        self.log.text_size = (self.width - 24, None)

    def _add_target(self, _button):
        url = self.url_input.text.strip()
        if not url:
            self.status.text = "请输入直播间地址"
            return
        self.controller.add_target(url, Quality.ORIGINAL, self.name_input.text.strip())
        self.url_input.text = ""
        self.name_input.text = ""
        self._render_targets()

    def _check_once(self, _button):
        self.status.text = "检测中"
        asyncio.run(self.controller.check_once())
        self._render_targets()

    def _stop_all(self, _button):
        self.controller.stop_all()
        self.status.text = "已停止全部录制"

    def _on_event(self, event):
        Clock.schedule_once(lambda _dt: self._append_log(f"{event.type.value}: {event.target_id} {event.message}"))

    def _append_log(self, text: str):
        self.log.text = f"{self.log.text}\n{text}".strip()

    def _render_targets(self):
        lines = ["直播间列表:"]
        for target in self.controller.targets:
            state = "启用" if target.enabled else "暂停"
            label = target.display_name or target.url
            lines.append(f"- {state} {label}")
        self.status.text = "\n".join(lines) if self.controller.targets else "还没有直播间"


class DouyinLiveRecorderDesktopApp(App):
    def build(self):
        root = Path.cwd()
        config_path = root / "config" / "config.ini"
        url_path = root / "config" / "URL_config.ini"
        event_bus = EventBus()
        app_config = ConfigService(config_path).load()
        scheduler = RecordingScheduler(
            registry=PlatformRegistry([DouyinAdapter()]),
            recorder=FFmpegRecorder(),
            event_bus=event_bus,
            config=SchedulerConfig(
                output_dir=Path(app_config.save_path or root / "downloads"),
                quality=app_config.quality,
                proxy_addr=app_config.proxy_addr if app_config.use_proxy else "",
                output_format=app_config.output_format,
                split_enabled=app_config.split_enabled,
                split_seconds=app_config.split_seconds,
            ),
        )
        controller = DesktopController(ConfigService(config_path), url_path, scheduler)
        return DesktopRoot(controller, event_bus)


def main() -> None:
    DouyinLiveRecorderDesktopApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run non-GUI tests**

Run:

```powershell
python -m unittest discover tests -v
```

Expected: all tests pass. This command does not import Kivy unless the desktop app file is imported directly.

- [ ] **Step 5: Manually smoke-test GUI import if Kivy is installed**

Run:

```powershell
python -c "from douyinliverecorder.apps.desktop.main import DouyinLiveRecorderDesktopApp; print(DouyinLiveRecorderDesktopApp.__name__)"
```

Expected when Kivy is installed: prints `DouyinLiveRecorderDesktopApp`.

Expected when Kivy is not installed: fails with `ModuleNotFoundError: No module named 'kivy'`. In that case, do not change source code; install GUI dependencies before manual GUI testing.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml requirements-gui.txt douyinliverecorder/apps/desktop/main.py
git commit -m "feat: add minimal desktop GUI entry point"
```

---

### Task 11: Desktop Packaging Scaffolds

**Files:**
- Create: `scripts/build_windows.ps1`
- Create: `scripts/build_linux.sh`
- Modify: `.gitignore`

- [ ] **Step 1: Add build artifact ignores**

Ensure `.gitignore` contains:

```gitignore
build/
dist/
*.spec
```

- [ ] **Step 2: Add Windows build script**

Create `scripts/build_windows.ps1`:

```powershell
$ErrorActionPreference = "Stop"

python -m pip install -r requirements-gui.txt
python -m PyInstaller `
  --name DouyinLiveRecorder `
  --onedir `
  --windowed `
  --add-data "config;config" `
  --add-data "i18n;i18n" `
  --add-data "src/javascript;src/javascript" `
  --collect-submodules kivy `
  -m douyinliverecorder.apps.desktop.main

Write-Host "Windows package created under dist/DouyinLiveRecorder"
```

- [ ] **Step 3: Add Linux build script**

Create `scripts/build_linux.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

python -m pip install -r requirements-gui.txt
python -m PyInstaller \
  --name DouyinLiveRecorder \
  --onedir \
  --windowed \
  --add-data "config:config" \
  --add-data "i18n:i18n" \
  --add-data "src/javascript:src/javascript" \
  --collect-submodules kivy \
  -m douyinliverecorder.apps.desktop.main

echo "Linux package created under dist/DouyinLiveRecorder"
```

- [ ] **Step 4: Run script syntax checks**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -WhatIf
```

Expected: PowerShell may report that `-WhatIf` is not consumed by the script. If so, run this parser-only check instead:

```powershell
$null = [System.Management.Automation.PSParser]::Tokenize((Get-Content scripts/build_windows.ps1 -Raw), [ref]$null)
```

Expected: no parser errors.

Run:

```powershell
Get-Content scripts/build_linux.sh | Select-String "PyInstaller"
```

Expected: one matching line.

- [ ] **Step 5: Commit**

```powershell
git add .gitignore scripts/build_windows.ps1 scripts/build_linux.sh
git commit -m "build: add desktop packaging scaffolds"
```

---

### Task 12: Full Slice Verification

**Files:**
- No new files.
- May modify files touched in earlier tasks only to fix verification failures.

- [ ] **Step 1: Run all unit tests**

Run:

```powershell
python -m unittest discover tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run legacy entry point import check**

Run:

```powershell
python -c "import main; print(main.version)"
```

Expected: importing `main` may start legacy side effects because the existing script has top-level loops. If it starts runtime behavior, interrupt it and record this as an existing legacy limitation. Do not refactor `main.py` in this plan.

- [ ] **Step 3: Run new package import check**

Run:

```powershell
python -c "from douyinliverecorder.platforms.douyin import DouyinAdapter; from douyinliverecorder.core.scheduler import RecordingScheduler; print(DouyinAdapter.key)"
```

Expected: prints `douyin`.

- [ ] **Step 4: Run GUI smoke test when dependencies are available**

Run:

```powershell
python -m douyinliverecorder.apps.desktop.main
```

Expected: Kivy window opens with URL input, note input, Add, Detect, and Stop buttons.

Manual check:

- Enter `https://live.douyin.com/123`.
- Click `添加直播间`.
- Confirm the target appears in the status area.
- Do not require a real live URL for this smoke test.

- [ ] **Step 5: Commit verification fixes**

If earlier steps required small fixes:

```powershell
git add douyinliverecorder tests pyproject.toml requirements-gui.txt scripts .gitignore
git commit -m "fix: stabilize desktop vertical slice"
```

If no fixes were required, skip the commit.

---

## Self-Review Checklist

Spec coverage in this first slice:

- Shared recording core: covered by Tasks 1, 2, 3, 4, and 8.
- Douyin adapter: covered by Task 6.
- FFmpeg runner wrapper: covered by Task 7.
- Windows/Linux GUI entry: covered by Tasks 9 and 10.
- Desktop packaging scaffold: covered by Task 11.
- Legacy `main.py` remains available: enforced by the "Do not modify" boundary and Task 12.

Out of scope for this first slice:

- Android foreground service and Android APK.
- TikTok, Kuaishou, Bilibili, Huya, Douyu, and YouTube adapters.
- Full settings UI, log search, push notification GUI, and remaining platform migration.

Those items need follow-up plans after this slice is merged and verified.
