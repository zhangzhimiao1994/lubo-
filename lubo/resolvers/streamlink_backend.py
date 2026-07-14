from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlparse

from lubo.resolvers.base import (
    PlatformAccessError,
    ResolverResult,
    ResolverStream,
    ResolverUnavailableError,
)


_HEIGHT_PATTERN = re.compile(r"(\d{3,4})p")
_ACCESS_ERROR_MESSAGE = "Streamlink could not access the live stream"


class StreamlinkBackend:
    def __init__(self, session_factory=None):
        self._session_factory = session_factory

    async def resolve(
        self,
        url: str,
        *,
        proxy_addr: str = "",
        cookies: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> ResolverResult:
        return await asyncio.to_thread(
            self._resolve_sync,
            url,
            proxy_addr=proxy_addr,
            cookies=cookies,
            headers=headers,
        )

    def _resolve_sync(
        self,
        url: str,
        *,
        proxy_addr: str = "",
        cookies: str = "",
        headers: Mapping[str, str] | None = None,
    ) -> ResolverResult:
        factory = self._session_factory
        if factory is None:
            try:
                from streamlink import Streamlink
            except (ImportError, ModuleNotFoundError):
                raise ResolverUnavailableError(
                    "Streamlink resolver dependency is unavailable"
                ) from None
            factory = Streamlink

        try:
            session = factory()
            if proxy_addr:
                session.set_option("http-proxy", proxy_addr)
            if headers:
                session.http.headers.update(headers)
            if cookies:
                parsed_cookies = SimpleCookie()
                parsed_cookies.load(cookies)
                session.http.cookies.update(
                    {name: morsel.value for name, morsel in parsed_cookies.items()}
                )

            _, plugin_class, resolved_url = session.resolve_url(url)
            plugin = plugin_class(session, resolved_url)
            streams = plugin.streams()
            anchor_name = _metadata_text(plugin, "author")
            title = _metadata_text(plugin, "title")
            if not streams:
                return ResolverResult(anchor_name=anchor_name, title=title)

            resolved_streams = tuple(
                resolved_stream
                for quality_name, stream in streams.items()
                if (
                    resolved_stream := _make_stream(
                        str(quality_name), stream.url
                    )
                )
                is not None
            )
            return ResolverResult(
                anchor_name=anchor_name,
                title=title,
                is_live=True,
                streams=resolved_streams,
            )
        except ResolverUnavailableError:
            raise
        except Exception:
            raise PlatformAccessError(_ACCESS_ERROR_MESSAGE) from None


def _metadata_text(metadata: Any, name: str) -> str:
    if isinstance(metadata, Mapping):
        value = metadata.get(name)
    else:
        value = getattr(metadata, name, "") if metadata is not None else ""
    if value is None:
        return ""
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("title") or ""
    try:
        return str(value)
    except Exception:
        return ""


def _make_stream(quality_name: str, stream_url: str) -> ResolverStream | None:
    match = _HEIGHT_PATTERN.search(quality_name)
    height = int(match.group(1)) if match else None
    parsed_url = urlparse(stream_url)
    if parsed_url.scheme.lower() not in {"http", "https"}:
        return None
    path = parsed_url.path.lower()
    if path.endswith(".flv"):
        protocol = "flv"
    elif path.endswith(".m3u8"):
        protocol = "hls"
    else:
        protocol = "http"
    return ResolverStream(
        url=stream_url,
        protocol=protocol,
        quality_name=quality_name,
        height=height,
    )
