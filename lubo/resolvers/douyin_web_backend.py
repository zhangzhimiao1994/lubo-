from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from http.cookiejar import CookieJar
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)

from lubo.resolvers.base import (
    PlatformAccessError,
    ResolverResult,
    ResolverStream,
)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_REFERER = "https://live.douyin.com/"
_ACCESS_ERROR_MESSAGE = "Douyin page could not be accessed"
_PARSE_ERROR_MESSAGE = "Douyin page could not be parsed"
_PACE_MARKER = "self.__pace_f.push("
_QUALITY_URL_KEYS = (
    ("origin", "FULL_HD1"),
    ("hd", "HD1"),
    ("sd", "SD1"),
    ("ld", "SD2"),
)
_RESOLUTION_PATTERN = re.compile(r"(\d{2,5})\s*[xX*]\s*(\d{2,5})")


PageFetcher = Callable[..., str]


class DouyinWebBackend:
    def __init__(
        self,
        fetcher: PageFetcher | None = None,
        *,
        timeout: float = 12.0,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than 0")
        self._fetcher = fetcher or _fetch_page
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes

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
        request_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": DEFAULT_REFERER,
        }
        if headers:
            request_headers.update(
                {str(name): str(value) for name, value in headers.items()}
            )
        if cookies:
            request_headers["Cookie"] = cookies

        try:
            page = self._fetcher(
                url,
                headers=request_headers,
                proxy_addr=proxy_addr,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
            )
            if isinstance(page, bytes):
                page = page.decode("utf-8")
            if not isinstance(page, str):
                raise TypeError("fetcher must return text")
        except Exception:
            raise PlatformAccessError(_ACCESS_ERROR_MESSAGE) from None

        try:
            room_info = _extract_room_info(page)
            room = _as_mapping(room_info.get("room"))
            if room is None:
                raise ValueError("missing room")
            anchor = _as_mapping(room_info.get("anchor")) or {}
            anchor_name = _metadata_text(anchor.get("nickname"))
            title = _metadata_text(room.get("title"))
            is_live = str(room.get("status", "")).strip() == "2"
            if not is_live:
                return ResolverResult(anchor_name=anchor_name, title=title)

            stream_url = _as_mapping(room.get("stream_url"))
            streams = _extract_streams(stream_url or {})
            return ResolverResult(
                anchor_name=anchor_name,
                title=title,
                is_live=True,
                streams=streams,
            )
        except Exception:
            raise PlatformAccessError(_PARSE_ERROR_MESSAGE) from None


def _fetch_page(
    url: str,
    *,
    headers: Mapping[str, str],
    proxy_addr: str,
    timeout: float,
    max_response_bytes: int,
) -> str:
    proxies = (
        {"http": proxy_addr, "https": proxy_addr} if proxy_addr else {}
    )
    opener = build_opener(
        ProxyHandler(proxies),
        HTTPCookieProcessor(CookieJar()),
    )
    request = Request(url, headers=dict(headers), method="GET")
    chunks: list[bytes] = []
    total = 0
    with opener.open(request, timeout=timeout) as response:
        while True:
            chunk = response.read(min(65536, max_response_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_response_bytes:
                raise ValueError("response is too large")
            chunks.append(chunk)
    return b"".join(chunks).decode("utf-8")


def _extract_room_info(page: str) -> Mapping[str, Any]:
    search_from = 0
    while True:
        marker_at = page.find(_PACE_MARKER, search_from)
        if marker_at < 0:
            break
        content_at = marker_at + len(_PACE_MARKER)
        call_content, search_from = _read_call_content(page, content_at)
        try:
            pushed = json.loads(call_content)
        except (TypeError, ValueError):
            continue
        room_info = _find_room_info(pushed)
        if room_info is not None:
            return room_info
    raise ValueError("missing Douyin room state")


def _read_call_content(page: str, start: int) -> tuple[str, int]:
    depth = 1
    quote = ""
    escaped = False
    index = start
    while index < len(page):
        character = page[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
        elif character in {'"', "'"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return page[start:index], index + 1
        index += 1
    raise ValueError("unterminated pace push")


def _find_room_info(value: Any, depth: int = 0) -> Mapping[str, Any] | None:
    if depth > 16:
        return None
    if isinstance(value, Mapping):
        room_store = _as_mapping(value.get("roomStore"))
        if room_store is not None:
            room_info = _as_mapping(room_store.get("roomInfo"))
            if room_info is not None and _as_mapping(room_info.get("room")) is not None:
                return room_info
        for nested in value.values():
            found = _find_room_info(nested, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_room_info(nested, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(value, str) and "roomStore" in value:
        decoder = json.JSONDecoder()
        for index, character in enumerate(value):
            if character not in "[{":
                continue
            try:
                decoded, _ = decoder.raw_decode(value, index)
            except ValueError:
                continue
            found = _find_room_info(decoded, depth + 1)
            if found is not None:
                return found
    return None


def _extract_streams(stream_url: Mapping[str, Any]) -> tuple[ResolverStream, ...]:
    flv_urls = _as_mapping(stream_url.get("flv_pull_url")) or {}
    hls_urls = _as_mapping(stream_url.get("hls_pull_url_map")) or {}
    heights = _quality_heights(stream_url)
    known_url_keys = {url_key for _, url_key in _QUALITY_URL_KEYS}
    ordered_qualities = list(_QUALITY_URL_KEYS)
    unknown_url_keys = sorted(
        {
            str(key)
            for key in (*flv_urls.keys(), *hls_urls.keys())
            if str(key) not in known_url_keys
        },
        key=str.casefold,
    )
    ordered_qualities.extend(
        (url_key.casefold(), url_key) for url_key in unknown_url_keys
    )

    streams: list[ResolverStream] = []
    stream_headers = {"User-Agent": DEFAULT_USER_AGENT}
    for quality_name, url_key in ordered_qualities:
        height = heights.get(quality_name)
        for protocol, url_map in (("flv", flv_urls), ("hls", hls_urls)):
            normalized_url = _normalize_cdn_url(url_map.get(url_key))
            if not normalized_url:
                continue
            streams.append(
                ResolverStream(
                    url=normalized_url,
                    protocol=protocol,
                    quality_name=quality_name,
                    height=height,
                    headers=stream_headers,
                )
            )
    return tuple(streams)


def _quality_heights(stream_url: Mapping[str, Any]) -> dict[str, int]:
    live_core = _as_mapping(stream_url.get("live_core_sdk_data")) or {}
    pull_data = _as_mapping(live_core.get("pull_data")) or {}
    options = _as_mapping(pull_data.get("options")) or {}
    qualities = options.get("qualities")
    if not isinstance(qualities, list):
        return {}

    heights: dict[str, int] = {}
    for quality in qualities:
        quality_data = _as_mapping(quality)
        if quality_data is None:
            continue
        sdk_key = _metadata_text(quality_data.get("sdk_key")).casefold()
        if sdk_key not in {quality for quality, _ in _QUALITY_URL_KEYS}:
            continue
        resolution = _metadata_text(quality_data.get("resolution"))
        match = _RESOLUTION_PATTERN.search(resolution)
        if match:
            heights[sdk_key] = min(int(match.group(1)), int(match.group(2)))
    return heights


def _normalize_cdn_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.casefold() not in {"http", "https"}:
            return ""
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return ""
        _ = parsed.port
    except (AttributeError, TypeError, ValueError):
        return ""
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _metadata_text(value: Any) -> str:
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return ""
    try:
        return str(value)
    except Exception:
        return ""
