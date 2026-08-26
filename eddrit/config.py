from environs import Env

env = Env()
env.read_env()

DEBUG: bool = env.bool("DEBUG", default=False)
LOG_LEVEL: str = env.str("LOG_LEVEL", default="WARNING")

VALKEY_URL: str = env.str("VALKEY_URL")
PROXY: str | None = env.str("PROXY", default=None)
PROXY_MEDIA: bool = env.bool("PROXY_MEDIA", default=False)
MEDIA_CACHE_ENABLED: bool = env.bool("MEDIA_CACHE_ENABLED", default=False)
MEDIA_CACHE_DIR: str = env.str("MEDIA_CACHE_DIR", default="/media-cache")
MEDIA_CACHE_MAX_MB: int = env.int("MEDIA_CACHE_MAX_MB", default=5120)
MEDIA_CACHE_DAYS: int = env.int("MEDIA_CACHE_DAYS", default=7)
