from __future__ import annotations

from lubo.platforms.bilibili import BilibiliAdapter
from lubo.platforms.douyin import DouyinAdapter
from lubo.platforms.douyu import DouyuAdapter
from lubo.platforms.huya import HuyaAdapter
from lubo.platforms.registry import PlatformRegistry
from lubo.resolvers.base import ResolverBackend
from lubo.resolvers.douyin_web_backend import DouyinWebBackend
from lubo.resolvers.streamlink_backend import StreamlinkBackend
from lubo.resolvers.yt_dlp_backend import YtDlpBackend


def build_default_registry(
    douyin_backend: ResolverBackend | None = None,
    streamlink_backend: ResolverBackend | None = None,
    yt_dlp_backend: ResolverBackend | None = None,
) -> PlatformRegistry:
    douyin = (
        douyin_backend
        if douyin_backend is not None
        else DouyinWebBackend()
    )
    streamlink = (
        streamlink_backend
        if streamlink_backend is not None
        else StreamlinkBackend()
    )
    yt_dlp = yt_dlp_backend if yt_dlp_backend is not None else YtDlpBackend()
    return PlatformRegistry(
        [
            DouyinAdapter(douyin),
            BilibiliAdapter(streamlink),
            HuyaAdapter(streamlink),
            DouyuAdapter(yt_dlp),
        ]
    )
