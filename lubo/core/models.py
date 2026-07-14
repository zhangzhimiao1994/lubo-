from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
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
    query_start = normalized.find("?")
    fragment_start = normalized.find("#")
    delimiter_candidates = [index for index in (query_start, fragment_start) if index != -1]
    delimiter_start = min(delimiter_candidates, default=len(normalized))
    if normalized and "://" not in normalized[:delimiter_start]:
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(slots=True)
class RecordingTask:
    target: RecordingTarget
    status: RecordingStatus = RecordingStatus.IDLE
    stream: StreamInfo | None = None
    output_path: Path | None = None
    started_at: float | None = None
    last_error: str = ""
    retry_count: int = 0
