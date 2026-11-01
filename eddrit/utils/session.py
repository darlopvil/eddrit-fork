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
"""

import httpx
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

# One client per worker process, created on first use (inside the worker's
# event loop) and closed by the app lifespan.
_client: httpx.AsyncClient | None = None


async def _raise_if_session_rate_limited(response: httpx.Response) -> None:
    """The session has its own quota (100 requests every 10 minutes). Reddit
    answers past it with an empty 429, which would otherwise fail as JSON."""
    if response.status_code == 429:
        logger.warning("REDDIT_SESSION reached Reddit's rate limit")
        raise RedditSessionRateLimitedError()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=get_httpx_async_transport(proxy=config.PROXY),
            headers={
                "Accept": "application/json",
                # A fixed header, not the cookie jar: cookies Reddit sets in
                # its responses must never replace the configured session.
                "Cookie": f"reddit_session={config.REDDIT_SESSION}",
            },
            event_hooks={
                "response": [
                    _raise_if_session_rate_limited,
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
    not set (same behaviour as before the session existed).
    """
    if not config.REDDIT_SESSION:
        return await http_client.get(f"{REDDIT_BASE_API_URL}{path}", headers=headers)

    try:
        return await _get_client().get(f"{REDDIT_WEB_URL}{path}", headers=headers)
    except RateLimitedError as e:
        # Never log the cookie itself
        logger.warning(
            "Reddit blocked a thread request made with REDDIT_SESSION: the "
            "session cookie has probably expired or been logged out"
        )
        raise RedditSessionRejectedError() from e


async def close() -> None:
    """Close the session client, if it was ever created."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
