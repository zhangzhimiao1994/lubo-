from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from lubo.resolvers.base import (
    PlatformAccessError,
    ResolverResult,
    ResolverStream,
    ResolverUnavailableError,
)


_ACCESS_ERROR_MESSAGE = "yt-dlp could not access the live stream"


class YtDlpBackend:
    def __init__(self, ydl_factory=None):
        self._ydl_factory = ydl_factory

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
        factory = self._ydl_factory
        if factory is None:
            try:
                from yt_dlp import YoutubeDL
            except (ImportError, ModuleNotFoundError):
                raise ResolverUnavailableError(
                    "yt-dlp resolver dependency is unavailable"
                ) from None
            factory = YoutubeDL

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "proxy": proxy_addr or None,
            "http_headers": {
                **dict(headers or {}),
                **({"Cookie": cookies} if cookies else {}),
            },
        }
        try:
            with factory(options) as ydl:
                info = ydl.extract_info(url, download=False)

            anchor_name = _first_text(info, "uploader", "channel", "creator")
            title = _text(info, "title")
            is_live = bool(_value(info, "is_live")) or (
                _value(info, "live_status") == "is_live"
            )
            if not is_live:
                return ResolverResult(anchor_name=anchor_name, title=title)

            streams = tuple(
                stream
                for format_info in (_value(info, "formats") or ())
                if (stream := _make_stream(format_info)) is not None
            )
            return ResolverResult(
                anchor_name=anchor_name,
                title=title,
                is_live=True,
                streams=streams,
            )
        except ResolverUnavailableError:
            raise
        except Exception as error:
            if _is_user_not_live_error(error):
                return ResolverResult()
            raise PlatformAccessError(_ACCESS_ERROR_MESSAGE) from None


def _value(info: Any, name: str) -> Any:
    if isinstance(info, Mapping):
        return info.get(name)
    return getattr(info, name, None) if info is not None else None


def _text(info: Any, name: str) -> str:
    value = _value(info, name)
    return str(value) if value is not None else ""


def _first_text(info: Any, *names: str) -> str:
    for name in names:
        value = _value(info, name)
        if value:
            return str(value)
    return ""


def _is_user_not_live_error(error: Exception) -> bool:
    return any(
        error_type.__name__ == "UserNotLive"
        and error_type.__module__.startswith("yt_dlp.utils")
        for error_type in type(error).__mro__
    )


def _normalize_height(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 and value.is_integer() else None
    if isinstance(value, str) and value.strip().isdigit():
        height = int(value.strip())
        return height if height > 0 else None
    return None


def _normalize_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: header_value
        for key, header_value in value.items()
        if isinstance(key, str) and isinstance(header_value, str)
    }


def _make_stream(format_info: Any) -> ResolverStream | None:
    if not isinstance(format_info, Mapping):
        return None
    stream_url = format_info.get("url")
    if not stream_url:
        return None

    protocol_name = str(format_info.get("protocol") or "").lower()
    extension = str(format_info.get("ext") or "").lower()
    if protocol_name in {"m3u8_native", "m3u8"}:
        protocol = "hls"
    elif extension == "flv":
        protocol = "flv"
    elif str(stream_url).lower().startswith(("http://", "https://")):
        protocol = "http"
    else:
        return None

    quality_name = format_info.get("format_note") or format_info.get("format") or ""
    return ResolverStream(
        url=str(stream_url),
        protocol=protocol,
        quality_name=str(quality_name),
        height=_normalize_height(format_info.get("height")),
        headers=_normalize_headers(format_info.get("http_headers")),
    )
