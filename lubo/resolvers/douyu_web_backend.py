from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from lubo.resolvers.base import PlatformAccessError, ResolverResult, ResolverStream


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_DEVICE_ID = "10000000000000000000000000001501"
_ACCESS_ERROR_MESSAGE = "Douyu API could not be accessed"
_PARSE_ERROR_MESSAGE = "Douyu API response could not be parsed"
_TRUSTED_INPUT_HOSTS = frozenset(
    {"douyu.com", "www.douyu.com", "m.douyu.com"}
)
_TRUSTED_STREAM_SUFFIXES = (".douyucdn.cn", ".douyucdn2.cn")
_OFFLINE_PREVIEW_ERRORS = frozenset({102, 104})


JsonFetcher = Callable[..., Mapping[str, Any]]


class DouyuWebBackend:
    def __init__(
        self,
        fetcher: JsonFetcher | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
        device_id: str = DEFAULT_DEVICE_ID,
        timeout: float = 12.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than 0")
        if not device_id:
            raise ValueError("device_id must not be empty")
        self._fetcher = fetcher or _fetch_json
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._device_id = device_id
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
        try:
            room_url, room_id = _validated_room_url(url)
        except Exception:
            raise PlatformAccessError(_ACCESS_ERROR_MESSAGE) from None

        request_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": room_url,
        }
        if headers:
            request_headers.update(
                {
                    str(name): str(value)
                    for name, value in headers.items()
                    if str(name).casefold() != "host"
                }
            )
        request_headers["Referer"] = room_url
        if cookies:
            request_headers["Cookie"] = cookies

        metadata_response = self._request_json(
            f"https://open.douyucdn.cn/api/RoomApi/room/{room_id}",
            method="GET",
            headers=request_headers,
            proxy_addr=proxy_addr,
        )
        try:
            metadata = _response_data(metadata_response)
            anchor_name = _text(metadata.get("owner_name"))
            title = _text(metadata.get("room_name"))
            if str(metadata.get("room_status", "")).strip() != "1":
                return ResolverResult(anchor_name=anchor_name, title=title)
        except Exception:
            raise PlatformAccessError(_PARSE_ERROR_MESSAGE) from None

        timestamp = str(int(self._clock_ms()))
        preview_headers = dict(request_headers)
        preview_headers.update(
            {
                "rid": room_id,
                "time": timestamp,
                "auth": hashlib.md5(f"{room_id}{timestamp}".encode()).hexdigest(),
            }
        )
        preview_response = self._request_json(
            "https://playweb.douyucdn.cn/lapi/live/"
            f"hlsH5Preview/{room_id}",
            method="POST",
            headers=preview_headers,
            data={"rid": room_id, "did": self._device_id},
            proxy_addr=proxy_addr,
        )
        try:
            preview_error = int(preview_response.get("error", -1))
            if preview_error in _OFFLINE_PREVIEW_ERRORS:
                return ResolverResult(anchor_name=anchor_name, title=title)
            if preview_error != 0:
                raise ValueError("preview request failed")
            preview = _response_data(preview_response)
            stream_url = _validated_stream_url(
                _text(preview.get("rtmp_url")),
                _text(preview.get("rtmp_live")),
            )
        except Exception:
            raise PlatformAccessError(_PARSE_ERROR_MESSAGE) from None

        stream_headers = {
            "User-Agent": request_headers["User-Agent"],
            "Referer": room_url,
        }
        return ResolverResult(
            anchor_name=anchor_name,
            title=title,
            is_live=True,
            streams=(
                ResolverStream(
                    url=stream_url,
                    protocol="hls",
                    quality_name="original",
                    headers=stream_headers,
                ),
            ),
        )

    def _request_json(self, url: str, **kwargs: Any) -> Mapping[str, Any]:
        try:
            response = self._fetcher(
                url,
                timeout=self._timeout,
                max_response_bytes=self._max_response_bytes,
                **kwargs,
            )
            if not isinstance(response, Mapping):
                raise TypeError("fetcher must return a mapping")
            return response
        except PlatformAccessError:
            raise
        except Exception:
            raise PlatformAccessError(_ACCESS_ERROR_MESSAGE) from None


def _fetch_json(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    proxy_addr: str,
    timeout: float,
    max_response_bytes: int,
    data: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    proxies = {"http": proxy_addr, "https": proxy_addr} if proxy_addr else {}
    opener = build_opener(ProxyHandler(proxies))
    body = urlencode(dict(data)).encode() if data is not None else None
    request_headers = dict(headers)
    if body is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=request_headers, method=method)
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(max_response_bytes + 1)
    if len(raw) > max_response_bytes:
        raise ValueError("response is too large")
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, Mapping):
        raise TypeError("JSON response must be an object")
    return parsed


def _validated_room_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.casefold()
        if scheme == "http":
            scheme = "https"
        if scheme != "https":
            raise ValueError("Douyu URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Douyu URL must not include user info")
        host = (parsed.hostname or "").casefold()
        if host not in _TRUSTED_INPUT_HOSTS:
            raise ValueError("untrusted Douyu host")
        if parsed.port not in (None, 443):
            raise ValueError("Douyu URL must use the default HTTPS port")
        room_id = parsed.path.strip("/").split("/", 1)[0]
        if not room_id.isdecimal():
            raise ValueError("Douyu room id must be numeric")
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid Douyu URL") from None
    return urlunsplit(("https", host, f"/{room_id}", "", "")), room_id


def _response_data(response: Mapping[str, Any]) -> Mapping[str, Any]:
    if int(response.get("error", -1)) != 0:
        raise ValueError("API request failed")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise TypeError("API data must be an object")
    return data


def _validated_stream_url(base_url: str, path: str) -> str:
    if not base_url or not path:
        raise ValueError("missing stream URL")
    combined = urljoin(base_url.rstrip("/") + "/", path)
    try:
        parsed = urlsplit(combined)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme.casefold() != "https":
            raise ValueError("stream URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("stream URL must not include user info")
        if parsed.port not in (None, 443):
            raise ValueError("stream URL must use the default HTTPS port")
        if not any(host.endswith(suffix) for suffix in _TRUSTED_STREAM_SUFFIXES):
            raise ValueError("untrusted stream host")
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid stream URL") from None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _text(value: Any) -> str:
    if value is None or isinstance(value, (Mapping, list, tuple)):
        return ""
    return str(value)
