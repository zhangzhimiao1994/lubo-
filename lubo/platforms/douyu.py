from lubo.platforms.engine import ResolverPlatformAdapter


class DouyuAdapter(ResolverPlatformAdapter):
    key = "douyu"
    display_name = "Douyu"
    domains = frozenset({"douyu.com", "www.douyu.com", "m.douyu.com"})
    referer = "https://www.douyu.com/"
