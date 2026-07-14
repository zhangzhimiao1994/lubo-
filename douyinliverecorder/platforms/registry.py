from __future__ import annotations

from typing import Iterable

from .base import PlatformAdapter


class PlatformRegistry:
    def __init__(self, adapters: Iterable[PlatformAdapter] | None = None) -> None:
        self._adapters = list(adapters or ())

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
