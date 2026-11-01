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
# Value of the reddit_session cookie of a logged-in (throwaway) account, used
# only for comment threads. See eddrit/utils/session.py
REDDIT_SESSION: str = env.str("REDDIT_SESSION", default="")
# Browser fingerprint (curl_cffi target) for the requests made with the session.
# Since 1 Nov 2026 www.reddit.com blocks Chrome fingerprints coming from
# datacenter IPs even with a valid session; Firefox and Safari pass. Kept
# configurable so it can be switched without a rebuild if that changes.
REDDIT_SESSION_IMPERSONATE: str = env.str(
    "REDDIT_SESSION_IMPERSONATE", default="firefox147"
)


def _frontend_url(name: str) -> str | None:
    """Base URL of a privacy frontend, without trailing slash. Empty = unset."""
    return env.str(name, default="").rstrip("/") or None


# Self-hosted frontends for third-party links and embeds. See
# eddrit/utils/frontends.py
INVIDIOUS_URL: str | None = _frontend_url("INVIDIOUS_URL")
NITTER_URL: str | None = _frontend_url("NITTER_URL")
PROXITOK_URL: str | None = _frontend_url("PROXITOK_URL")
RIMGO_URL: str | None = _frontend_url("RIMGO_URL")
PRIVIBLUR_URL: str | None = _frontend_url("PRIVIBLUR_URL")
SOPRANO_URL: str | None = _frontend_url("SOPRANO_URL")
