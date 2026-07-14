from lubo.platforms.engine import ResolverPlatformAdapter


class BilibiliAdapter(ResolverPlatformAdapter):
    key = "bilibili"
    display_name = "Bilibili Live"
    domains = frozenset({"live.bilibili.com"})
    referer = "https://live.bilibili.com/"
