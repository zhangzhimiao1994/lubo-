from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from lubo.core.models import Quality, RecordingTarget, StreamInfo, normalize_url
from lubo.platforms.base import ResolveContext


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


def _url_parts(url: str) -> tuple[str, str]:
    parsed = urlsplit(normalize_url(url))
    return (parsed.hostname or "").lower(), parsed.path


async def _default_web_fetcher(url: str, proxy_addr: str, cookies: str) -> dict:
    from src import spider

    return await spider.get_douyin_web_stream_data(url, proxy_addr, cookies)


async def _default_app_fetcher(url: str, proxy_addr: str, cookies: str) -> dict:
    from src import spider

    return await spider.get_douyin_app_stream_data(url, proxy_addr, cookies)


async def _default_stream_resolver(json_data: dict, video_quality: str, proxy_addr: str) -> dict:
    from src import stream

    return await stream.get_douyin_stream_url(json_data, video_quality, proxy_addr)


class DouyinAdapter:
    key = "douyin"
    display_name = "Douyin"

    def __init__(
        self,
        web_fetcher: FetchFn | None = None,
        app_fetcher: FetchFn | None = None,
        stream_resolver: StreamFn | None = None,
    ) -> None:
        self._web_fetcher = web_fetcher or _default_web_fetcher
        self._app_fetcher = app_fetcher or _default_app_fetcher
        self._stream_resolver = stream_resolver or _default_stream_resolver

    def matches(self, url: str) -> bool:
        hostname, _path = _url_parts(url)
        return hostname in {"live.douyin.com", "v.douyin.com", "www.douyin.com"}

    async def resolve(self, target: RecordingTarget, context: ResolveContext) -> StreamInfo:
        cookies = context.cookie_value("douyin")
        proxy = context.proxy_addr
        hostname, path = _url_parts(target.url)
        if hostname == "v.douyin.com" or "/user/" in path.lower():
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
