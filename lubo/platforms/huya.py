from lubo.platforms.engine import ResolverPlatformAdapter


class HuyaAdapter(ResolverPlatformAdapter):
    key = "huya"
    display_name = "Huya"
    domains = frozenset({"huya.com", "www.huya.com", "m.huya.com"})
    referer = "https://www.huya.com/"
