from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol

from lubo.core.models import Quality, RecordingTarget, StreamInfo


class UnsupportedPlatformError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Unsupported platform URL")


@dataclass(frozen=True, slots=True)
class ResolveContext:
    quality: Quality = Quality.ORIGINAL
    proxy_addr: str = ""
    cookies: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.cookies is not None:
            object.__setattr__(self, "cookies", MappingProxyType(dict(self.cookies)))

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
