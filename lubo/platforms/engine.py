from __future__ import annotations

from urllib.parse import urlsplit

from lubo.core.models import RecordingTarget, StreamInfo, normalize_url
from lubo.platforms.base import ResolveContext
from lubo.resolvers.base import ResolverBackend, ResolverStream
from lubo.resolvers.selection import select_stream


class ResolverPlatformAdapter:
    key: str
    display_name: str
    domains: frozenset[str]
    referer: str

    def __init__(self, backend: ResolverBackend) -> None:
        self.backend = backend

    def matches(self, url: str) -> bool:
        try:
            normalized = normalize_url(url)
            if "\\" in normalized:
                return False
            parsed = urlsplit(normalized)
            if parsed.scheme.lower() not in {"http", "https"}:
                return False
            if parsed.username is not None or parsed.password is not None:
                return False
            _ = parsed.port
            hostname = parsed.hostname
        except (AttributeError, TypeError, ValueError):
            return False
        return bool(hostname) and hostname.lower() in self.domains

    async def resolve(
        self, target: RecordingTarget, context: ResolveContext
    ) -> StreamInfo:
        result = await self.backend.resolve(
            target.url,
            proxy_addr=context.proxy_addr,
            cookies=context.cookie_value(self.key),
            headers={"Referer": self.referer},
        )
        common = {
            "platform_key": self.key,
            "platform_name": self.display_name,
            "anchor_name": result.anchor_name,
            "title": result.title,
            "quality": context.quality,
            "is_live": result.is_live,
        }
        if not result.is_live:
            return StreamInfo(**common, headers={"Referer": self.referer})

        selected = select_stream(result.streams, context.quality)
        headers = {**dict(selected.headers), "Referer": self.referer}
        return StreamInfo(
            **common,
            primary_url=selected.url,
            flv_url=_protocol_url(result.streams, "flv", selected),
            hls_url=_protocol_url(result.streams, "hls", selected),
            headers=headers,
        )


def _protocol_url(
    streams: tuple[ResolverStream, ...],
    protocol: str,
    selected: ResolverStream,
) -> str:
    candidates = tuple(
        stream
        for stream in streams
        if stream.url and stream.protocol.lower() == protocol
    )
    if not candidates:
        return ""
    for stream in candidates:
        if stream.height == selected.height:
            return stream.url
    return candidates[0].url
