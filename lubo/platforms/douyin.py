from lubo.platforms.engine import ResolverPlatformAdapter
from lubo.resolvers.base import ResolverBackend
from lubo.resolvers.douyin_web_backend import DouyinWebBackend


class DouyinAdapter(ResolverPlatformAdapter):
    key = "douyin"
    display_name = "Douyin"
    domains = frozenset(
        {"live.douyin.com", "v.douyin.com", "www.douyin.com"}
    )
    referer = "https://live.douyin.com/"

    def __init__(self, backend: ResolverBackend | None = None) -> None:
        super().__init__(backend if backend is not None else DouyinWebBackend())
