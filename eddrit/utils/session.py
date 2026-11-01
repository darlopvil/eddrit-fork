"""
Logged-in Reddit session, used only for comment threads.

Since 20 Oct 2026 Reddit answers every comments route (`/r/<sub>/comments/...`)
with its "blocked by network security" 403 page when it is called with the
anonymous token of the Android app, on oauth.reddit.com and www.reddit.com
alike and from any exit IP. Listings, subreddit info and `/api/info` keep
working. With the `reddit_session` cookie of a logged-in account the same
routes answer normally on www.reddit.com.

Opt-in through REDDIT_SESSION. Everything else stays anonymous, so the account
only ever sees which threads are opened. Use a throwaway account.

The account is also protected from being used up: threads are cached for a
few minutes (reloading or going back costs nothing), and when Reddit says the
session's quota is running low, eddrit stops using it until the window resets
instead of draining it. On 1 Nov 2026 a scraper spent the whole quota of the
first throwaway account (about 100 threads every 10 minutes for hours).
"""

import zlib

import httpx
import valkey.asyncio as valkey_asyncio
from loguru import logger

from eddrit import config
from eddrit.constants import REDDIT_BASE_API_URL
from eddrit.exceptions import (
    RateLimitedError,
    RedditSessionRateLimitedError,
    RedditSessionRejectedError,
)
from eddrit.utils.httpx import (
    get_httpx_async_transport,
    raise_if_content_is_not_available,
)

REDDIT_WEB_URL = "https://www.reddit.com"

# Reddit gives a session 100 requests every 10 minutes and reports what is
# left in x-ratelimit-remaining. Below this many, eddrit stops using the
# session until the window resets, so the account never runs dry.
SESSION_QUOTA_FLOOR = 20

_PAUSE_KEY = "session_quota_pause_v1"
_CACHE_PREFIX = "thread_cache_v1:"

# One client per worker process, created on first use (inside the worker's
# event loop) and closed by the app lifespan.
_client: httpx.AsyncClient | None = None
_valkey: valkey_asyncio.Valkey | None = None


def _get_valkey() -> valkey_asyncio.Valkey:
    global _valkey
    if _valkey is None:
        _valkey = valkey_asyncio.from_url(config.VALKEY_URL)
    return _valkey


async def _pause_session(seconds: int) -> None:
    try:
        await _get_valkey().set(_PAUSE_KEY, "1", ex=max(seconds, 1))
    except Exception:
        logger.exception("Cannot store the session pause in Valkey")


async def _session_paused_for() -> int:
    """Seconds left of a pause of the session, 0 if it is not paused."""
    try:
        ttl = await _get_valkey().ttl(_PAUSE_KEY)
    except Exception:
        logger.exception("Cannot read the session pause from Valkey")
        return 0
    return max(0, ttl)


async def _cache_get(path: str) -> tuple[bytes, str] | None:
    if config.THREAD_CACHE_SECONDS <= 0:
        return None
    try:
        cached = await _get_valkey().hgetall(_CACHE_PREFIX + path)
    except Exception:
        logger.exception("Cannot read the thread cache from Valkey")
        return None
    if not cached:
        return None
    return zlib.decompress(cached[b"body"]), cached[b"type"].decode()


async def _cache_put(path: str, response: httpx.Response) -> None:
    if config.THREAD_CACHE_SECONDS <= 0 or response.status_code != 200:
        return
    key = _CACHE_PREFIX + path
    try:
        async with _get_valkey().pipeline(transaction=True) as pipe:
            pipe.hset(
                key,
                mapping={
                    "body": zlib.compress(response.content),
                    "type": response.headers.get("content-type", "application/json"),
                },
            )
            pipe.expire(key, config.THREAD_CACHE_SECONDS)
            await pipe.execute()
    except Exception:
        logger.exception("Cannot store a thread in the Valkey cache")


async def _raise_if_session_rate_limited(response: httpx.Response) -> None:
    """Past the quota Reddit answers with an empty 429, which would otherwise
    fail as JSON. It does not say when the window resets: pause 10 minutes,
    the length of a whole window."""
    if response.status_code == 429:
        logger.warning("REDDIT_SESSION reached Reddit's rate limit")
        await _pause_session(600)
        raise RedditSessionRateLimitedError(retry_after=600)


async def _track_session_quota(response: httpx.Response) -> None:
    """Pause the session when Reddit reports that its quota is running low."""
    remaining = response.headers.get("x-ratelimit-remaining")
    reset = response.headers.get("x-ratelimit-reset")
    if remaining is None or reset is None:
        return
    try:
        remaining_value = float(remaining)
        reset_seconds = int(float(reset)) + 1
    except ValueError:
        return
    if remaining_value < SESSION_QUOTA_FLOOR:
        logger.warning(
            f"REDDIT_SESSION has {remaining_value:.0f} requests left in Reddit's "
            f"window: pausing it for {reset_seconds}s"
        )
        await _pause_session(reset_seconds)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=get_httpx_async_transport(
                proxy=config.PROXY, impersonate=config.REDDIT_SESSION_IMPERSONATE
            ),
            headers={
                "Accept": "application/json",
                # A fixed header, not the cookie jar: cookies Reddit sets in
                # its responses must never replace the configured session.
                "Cookie": f"reddit_session={config.REDDIT_SESSION}",
            },
            event_hooks={
                "response": [
                    _raise_if_session_rate_limited,
                    _track_session_quota,
                    raise_if_content_is_not_available,
                ]
            },
        )
    return _client


async def get_thread(
    http_client: httpx.AsyncClient,
    path: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET a comments route: with the session when configured, else anonymously.

    `http_client` is the app's anonymous client, used when REDDIT_SESSION is
    not set (same behaviour as before the session existed). Either way the
    answer is cached for THREAD_CACHE_SECONDS.
    """
    base_url = REDDIT_WEB_URL if config.REDDIT_SESSION else REDDIT_BASE_API_URL
    if cached := await _cache_get(path):
        body, content_type = cached
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type},
            request=httpx.Request("GET", f"{base_url}{path}"),
        )

    if not config.REDDIT_SESSION:
        response = await http_client.get(f"{base_url}{path}", headers=headers)
        await _cache_put(path, response)
        return response

    if paused_for := await _session_paused_for():
        raise RedditSessionRateLimitedError(retry_after=paused_for)

    try:
        response = await _get_client().get(f"{base_url}{path}", headers=headers)
    except RateLimitedError as e:
        # Never log the cookie itself
        logger.warning(
            "Reddit blocked a thread request made with REDDIT_SESSION: the "
            "cookie has expired or Reddit blocks the fingerprint "
            "(REDDIT_SESSION_IMPERSONATE)"
        )
        raise RedditSessionRejectedError() from e
    await _cache_put(path, response)
    return response


async def close() -> None:
    """Close the session and Valkey clients, if they were ever created."""
    global _client, _valkey
    if _client is not None:
        await _client.aclose()
        _client = None
    if _valkey is not None:
        await _valkey.aclose()
        _valkey = None
