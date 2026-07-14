from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol


class ResolverError(RuntimeError):
    pass


class ResolverUnavailableError(ResolverError):
    pass


class PlatformAccessError(ResolverError):
    pass


class NoCompatibleStreamError(ResolverError):
    pass


@dataclass(frozen=True, slots=True)
class ResolverStream:
    url: str
    protocol: str
    quality_name: str = ""
    height: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class ResolverResult:
    anchor_name: str = ""
    title: str = ""
    is_live: bool = False
    streams: tuple[ResolverStream, ...] = ()


class ResolverBackend(Protocol):
    async def resolve(
        self,
        url: str,
        *,
        proxy_addr: str = "",
        cookies: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> ResolverResult: ...
