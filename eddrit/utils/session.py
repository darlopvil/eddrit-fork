"""
Logged-in Reddit session, used for comment threads and for the API routes
that Reddit refuses to the anonymous token (search).

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

Since 11 Nov 2026 Reddit also refuses post search (`/search.json`, on every
host and path variant) to the anonymous token, and www.reddit.com refuses all
searches to sessions. The API (oauth.reddit.com) still answers them to a
token minted for the session, the way the Android app logs in, so get_api()
tries the anonymous token first and falls back to that token. Same quota as
the cookie (the account's 100 requests every 10 minutes), same pause.
"""

import json
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
from eddrit.utils.oauth import android_app_basic_auth, android_app_identity

REDDIT_WEB_URL = "https://www.reddit.com"

# Reddit gives a session 100 requests every 10 minutes and reports what is
# left in x-ratelimit-remaining. Below this many, eddrit stops using the
# session until the window resets, so the account never runs dry.
SESSION_QUOTA_FLOOR = 20

# After Reddit blocks an API route for the anonymous token, it goes straight
# to the session's token for this long before the anonymous token is tried
# again: every blocked try costs two requests (see RecyclingAsyncTransport).
ANON_BLOCK_SECONDS = 1800

_PAUSE_KEY = "session_quota_pause_v1"
_CACHE_PREFIX = "thread_cache_v1:"
_TOKEN_KEY = "session_api_token_v1"  # noqa: S105 (a Valkey key name)
_ANON_BLOCK_PREFIX = "anon_api_blocked_v1:"

# One client per worker process, created on first use (inside the worker's
# event loop) and closed by the app lifespan.
_client: httpx.AsyncClient | None = None
_api_client: httpx.AsyncClient | None = None
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


def _get_api_client() -> httpx.AsyncClient:
    """Client for oauth.reddit.com with the session's token: no cookie, the
    token is the credential. Same fingerprint and quota hooks as _client."""
    global _api_client
    if _api_client is None:
        _api_client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=get_httpx_async_transport(
                proxy=config.PROXY, impersonate=config.REDDIT_SESSION_IMPERSONATE
            ),
            headers={"Accept": "application/json"},
            event_hooks={
                "response": [
                    _raise_if_session_rate_limited,
                    _track_session_quota,
                    raise_if_content_is_not_available,
                ]
            },
        )
    return _api_client


async def _session_api_headers(refresh: bool = False) -> dict[str, str]:
    """Headers for oauth.reddit.com with a token minted for the session.

    Minted like the Android app does after logging in, and kept in Valkey
    until 5 minutes before it expires (about a day). Never logged.
    """
    valkey_client = _get_valkey()
    if not refresh:
        try:
            if cached := await valkey_client.get(_TOKEN_KEY):
                return json.loads(cached)
        except Exception:
            logger.exception("Cannot read the session's API token from Valkey")

    identity = android_app_identity()
    try:
        res = await _get_client().post(
            f"{REDDIT_WEB_URL}/auth/v2/oauth/access-token/session",
            json={"scopes": ["*", "email", "pii"]},
            headers={**identity, "Authorization": android_app_basic_auth()},
        )
    except RateLimitedError as e:
        logger.warning("Reddit blocked minting an API token for REDDIT_SESSION")
        raise RedditSessionRejectedError() from e
    token = res.json().get("access_token") if res.status_code == 200 else None
    if not token:
        logger.warning(
            f"Reddit did not mint an API token for REDDIT_SESSION ({res.status_code})"
        )
        raise RedditSessionRejectedError()

    headers = {**identity, "Authorization": f"Bearer {token}"}
    try:
        await valkey_client.set(
            _TOKEN_KEY,
            json.dumps(headers),
            ex=max(int(res.json().get("expires_in", 3600)) - 300, 60),
        )
    except Exception:
        logger.exception("Cannot store the session's API token in Valkey")
    return headers


async def _anon_blocked(route: str) -> bool:
    try:
        return bool(await _get_valkey().exists(_ANON_BLOCK_PREFIX + route))
    except Exception:
        logger.exception("Cannot read an anonymous block from Valkey")
        return False


async def _mark_anon_blocked(route: str) -> None:
    try:
        await _get_valkey().set(_ANON_BLOCK_PREFIX + route, "1", ex=ANON_BLOCK_SECONDS)
    except Exception:
        logger.exception("Cannot store an anonymous block in Valkey")


async def get_api(http_client: httpx.AsyncClient, path: str) -> httpx.Response:
    """GET an API route (oauth.reddit.com) that Reddit may refuse to the
    anonymous token, such as search.

    Anonymous first, with `http_client`, the app's client. When Reddit blocks
    the route for it and REDDIT_SESSION is set, the request is repeated with
    the session's API token, and that route skips the anonymous token for
    ANON_BLOCK_SECONDS. A token Reddit no longer accepts is minted again once.
    Answers are cached for THREAD_CACHE_SECONDS.
    """
    url = f"{REDDIT_BASE_API_URL}{path}"
    if cached := await _cache_get(path):
        body, content_type = cached
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": content_type},
            request=httpx.Request("GET", url),
        )

    route = path.split("?", 1)[0]
    if not config.REDDIT_SESSION or not await _anon_blocked(route):
        try:
            response = await http_client.get(url)
        except RateLimitedError:
            if not config.REDDIT_SESSION:
                raise
            logger.warning(
                f"Reddit blocks {route} for the anonymous token: using the "
                f"session's API token for {ANON_BLOCK_SECONDS // 60} min"
            )
            await _mark_anon_blocked(route)
        else:
            await _cache_put(path, response)
            return response

    if paused_for := await _session_paused_for():
        raise RedditSessionRateLimitedError(retry_after=paused_for)

    try:
        response = await _get_api_client().get(
            url, headers=await _session_api_headers()
        )
        if response.status_code == 401:
            # Expired or revoked before its time: mint a new one, once
            response = await _get_api_client().get(
                url, headers=await _session_api_headers(refresh=True)
            )
    except RateLimitedError as e:
        logger.warning(f"Reddit blocked {route} with the session's API token too")
        raise RedditSessionRejectedError() from e
    await _cache_put(path, response)
    return response


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
    global _client, _api_client, _valkey
    if _client is not None:
        await _client.aclose()
        _client = None
    if _api_client is not None:
        await _api_client.aclose()
        _api_client = None
    if _valkey is not None:
        await _valkey.aclose()
        _valkey = None
